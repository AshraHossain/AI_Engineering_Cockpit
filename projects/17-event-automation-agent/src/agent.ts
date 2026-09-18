/**
 * Event-triggered automation agent — SOC / security-operations skeleton.
 *
 * Layering (each layer knows only the interface below it):
 *
 *     EventSource        ingest: webhook receiver, queue consumer, SIEM stream
 *         |
 *     TriggerEvaluator   rules: which workflows does this alert deserve?
 *         |
 *     WorkflowExecutor   run: enrich, contain, open case — with retries
 *         |   \
 *         |    DeadLetterQueue   permanently failed (event, workflow) pairs
 *     IdempotencyStore   exactly-once-ish: one detection => one containment
 *         |
 *     MetricsLogger      cross-cutting observability hook
 *
 * Extension points are marked `TODO(integration)`. Nothing here opens a
 * socket; wire your transport into an EventSource and your SOAR actions
 * into Workflow implementations.
 *
 * Uses only erasable TypeScript syntax (no enums, no constructor parameter
 * properties), so Node 22.18+ runs it directly without a build step.
 */

// --------------------------------------------------------------------------- //
// Domain
// --------------------------------------------------------------------------- //

/**
 * A single security signal to be acted on.
 *
 * `id` is the idempotency key. It MUST be stable across redeliveries — use
 * the detection ID from the SIEM/EDR, not a locally generated UUID, or
 * at-least-once delivery will double-execute containment.
 */
export interface AutomationEvent {
  readonly id: string;
  /** "edr" | "siem" | "cloudtrail" | "phishing-mailbox" | ... */
  readonly source: string;
  /** "alert.raised" | "finding.created" | ... */
  readonly type: string;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly receivedAt: number;
}

/**
 * Throw from a Workflow when retrying cannot possibly help.
 *
 * Examples: malformed alert schema, host no longer exists, policy forbids
 * the action. These bypass the RetryPolicy and go straight to the DLQ.
 */
export class PermanentError extends Error {
  override readonly name = 'PermanentError';
}

// --------------------------------------------------------------------------- //
// Ingest
// --------------------------------------------------------------------------- //

/**
 * Transport abstraction. One implementation per ingest mechanism.
 *
 * Implementations may be at-least-once; exactly-once is provided upstack by
 * the IdempotencyStore, not by the transport.
 */
export interface EventSource {
  /**
   * Yield events until the source is drained or the consumer breaks.
   *
   * TODO(integration): webhook source — push received requests onto an async
   * queue from the HTTP handler and yield from it here, so the HTTP response
   * returns before workflows run.
   * TODO(integration): queue source — long-poll SQS / consume Kafka / pull
   * Pub/Sub, yielding one event per message.
   */
  events(): AsyncIterable<AutomationEvent>;

  /** Confirm terminal handling. The broker must not redeliver. */
  ack(event: AutomationEvent): Promise<void>;

  /** Return the event for redelivery (agent fault, or leased elsewhere). */
  nack(event: AutomationEvent, reason: unknown): Promise<void>;
}

// --------------------------------------------------------------------------- //
// Idempotency
// --------------------------------------------------------------------------- //

/**
 * Outcome of `IdempotencyStore.claim`.
 *
 * 'settled' and 'leased' must stay distinct: a settled event is safe to ack,
 * but acking a *leased* one would drop it if its holder then crashed.
 *
 * - 'acquired': caller owns the event now
 * - 'settled':  already done or dead-lettered — ack and drop
 * - 'leased':   another worker holds it — let the broker redeliver
 */
export type Claim = 'acquired' | 'settled' | 'leased';

/** Nack reason when another worker currently holds the event's lease. */
export class LeaseHeldError extends Error {
  override readonly name = 'LeaseHeldError';
  readonly eventId: string;

  constructor(eventId: string) {
    super(`event ${eventId} is leased by another worker`);
    this.eventId = eventId;
  }
}

/**
 * Dedup ledger keyed by `AutomationEvent.id`.
 *
 * The contract is a lease, not a flag: `claim` grants exclusive ownership
 * for `ttlMs`, so a crashed agent's events become claimable again instead
 * of being stranded forever.
 *
 * TODO(integration): a distributed store should return a lease token from
 * `claim` and fence `complete`/`fail`/`release` on it, so a worker whose
 * lease expired mid-workflow cannot settle a claim that another worker has
 * since taken over.
 */
export interface IdempotencyStore {
  /**
   * Try to take exclusive ownership of the event. Must be atomic across
   * processes.
   *
   * TODO(integration): Redis `SET key value NX PX ttl`, or an INSERT against
   * a UNIQUE column with `ON CONFLICT DO NOTHING`.
   */
  claim(eventId: string, ttlMs: number): Promise<Claim>;

  /** Terminal success. Later redeliveries are skipped. */
  complete(eventId: string): Promise<void>;

  /**
   * Terminal failure (dead-lettered). Also skipped on redelivery — the DLQ
   * owns it now; replaying is a deliberate operator action.
   */
  fail(eventId: string): Promise<void>;

  /** Drop the lease without a verdict, so the broker may redeliver. */
  release(eventId: string): Promise<void>;
}

/** Expiry marker for done / dead-lettered events. */
const SETTLED = Number.POSITIVE_INFINITY;

/**
 * Reference implementation for tests and single-process runs.
 *
 * ponytail: process-local Map — gives you no dedup across replicas. Swap for
 * Redis/Postgres before running more than one agent.
 */
export class InMemoryIdempotencyStore implements IdempotencyStore {
  /** eventId -> lease expiry (epoch ms), or SETTLED. */
  private readonly entries = new Map<string, number>();

  // Node's single-threaded event loop makes check-then-set atomic here; a
  // distributed implementation must push that atomicity into the backend.
  async claim(eventId: string, ttlMs: number): Promise<Claim> {
    const expiresAt = this.entries.get(eventId);
    const now = Date.now();
    if (expiresAt === SETTLED) return 'settled';
    if (expiresAt !== undefined && now < expiresAt) return 'leased';
    this.entries.set(eventId, now + ttlMs);
    return 'acquired';
  }

  async complete(eventId: string): Promise<void> {
    this.settle(eventId);
  }

  async fail(eventId: string): Promise<void> {
    // Done vs dead only matters to a ledger someone queries; the DLQ already
    // holds the failure detail.
    this.settle(eventId);
  }

  async release(eventId: string): Promise<void> {
    this.entries.delete(eventId);
  }

  private settle(eventId: string): void {
    // TODO(integration): settled rows need a retention TTL of their own
    // (e.g. 7d) or the ledger grows without bound.
    this.entries.set(eventId, SETTLED);
  }
}

// --------------------------------------------------------------------------- //
// Triggers
// --------------------------------------------------------------------------- //

/**
 * Binds a predicate over an event to the workflows it should fire.
 *
 * Keep predicates pure and cheap — they run on every event. No I/O here.
 */
export interface TriggerRule {
  readonly name: string;
  matches(event: AutomationEvent): boolean;
  readonly workflows: readonly string[];
}

/**
 * Maps one event to an ordered, de-duplicated list of workflow names.
 *
 * Extension point: subclass and override `evaluate` to read rules from a
 * database or a policy DSL instead of holding them in memory.
 */
export class TriggerEvaluator {
  private readonly rules: readonly TriggerRule[];

  constructor(rules: readonly TriggerRule[]) {
    this.rules = rules;
  }

  evaluate(event: AutomationEvent): string[] {
    const selected = new Set<string>(); // Set preserves insertion order
    for (const rule of this.rules) {
      let fired: boolean;
      try {
        fired = rule.matches(event);
      } catch (err) {
        // A broken rule must not silence every other rule.
        console.error(`trigger rule ${rule.name} threw; skipping`, err);
        continue;
      }
      if (fired) for (const w of rule.workflows) selected.add(w);
    }
    return [...selected];
  }
}

// --------------------------------------------------------------------------- //
// Workflows
// --------------------------------------------------------------------------- //

/** Per-attempt context handed to a Workflow. */
export interface WorkflowContext {
  readonly event: AutomationEvent;
  /** 1-based. */
  readonly attempt: number;
  readonly metrics: MetricsLogger;
}

/**
 * One unit of SOC automation: enrich, contain, notify, open case.
 *
 * Implementations MUST be safe to re-run: the RetryPolicy will invoke `run`
 * again after a transient failure, possibly after a partial side effect.
 * Guard external calls with their own request IDs.
 */
export interface Workflow {
  readonly name: string;

  /**
   * Perform the action. Throw PermanentError to skip retries.
   *
   * TODO(integration): enrichment — threat-intel lookup on observables.
   * TODO(integration): containment — EDR host isolation, IAM key revoke.
   * TODO(integration): case management — create/annotate the ticket.
   */
  run(ctx: WorkflowContext): Promise<void>;
}

export interface RetryOptions {
  readonly maxAttempts?: number;
  readonly baseDelayMs?: number;
  readonly maxDelayMs?: number;
}

/**
 * Exponential backoff with full jitter.
 *
 * Full jitter (uniform over [0, ceiling]) rather than fixed backoff: a SIEM
 * outage delivers a burst of correlated alerts, and undithered retries would
 * reconverge into a thundering herd against the very API that just failed.
 */
export class RetryPolicy {
  readonly maxAttempts: number;
  readonly baseDelayMs: number;
  readonly maxDelayMs: number;

  constructor(options: RetryOptions = {}) {
    this.maxAttempts = options.maxAttempts ?? 5;
    this.baseDelayMs = options.baseDelayMs ?? 200;
    this.maxDelayMs = options.maxDelayMs ?? 30_000;
  }

  shouldRetry(attempt: number, err: unknown): boolean {
    if (err instanceof PermanentError) return false;
    return attempt < this.maxAttempts;
  }

  delayFor(attempt: number): number {
    const ceiling = Math.min(this.maxDelayMs, this.baseDelayMs * 2 ** (attempt - 1));
    return Math.random() * ceiling;
  }
}

/** A (event, workflow) pair that exhausted retries or failed hard. */
export interface DeadLetter {
  readonly event: AutomationEvent;
  readonly workflow: string;
  readonly attempts: number;
  readonly error: string;
  readonly failedAt: number;
}

/** Terminal sink for unprocessable work. */
export interface DeadLetterQueue {
  /**
   * TODO(integration): SQS DLQ, Kafka topic, or a `dead_letters` table an
   * analyst can triage and replay from.
   */
  send(letter: DeadLetter): Promise<void>;
}

export class InMemoryDeadLetterQueue implements DeadLetterQueue {
  readonly letters: DeadLetter[] = [];

  async send(letter: DeadLetter): Promise<void> {
    this.letters.push(letter);
    console.error(
      `dead-letter event=${letter.event.id} workflow=${letter.workflow} ` +
        `attempts=${letter.attempts} error=${letter.error}`,
    );
  }
}

/**
 * Runs the selected workflows for an event under a RetryPolicy.
 *
 * Workflows run sequentially and independently: one failing workflow is
 * dead-lettered on its own and does not cancel its siblings. Swap in
 * `Promise.all` here if your workflows are order-independent and latency
 * matters more than a predictable audit trail.
 */
export class WorkflowExecutor {
  private readonly workflows: ReadonlyMap<string, Workflow>;
  private readonly retry: RetryPolicy;
  private readonly dlq: DeadLetterQueue;
  private readonly metrics: MetricsLogger;

  constructor(
    workflows: Iterable<Workflow>,
    retry: RetryPolicy,
    dlq: DeadLetterQueue,
    metrics: MetricsLogger,
  ) {
    this.workflows = new Map([...workflows].map((w) => [w.name, w]));
    this.retry = retry;
    this.dlq = dlq;
    this.metrics = metrics;
  }

  /** Run each named workflow. Resolves to the names that failed terminally. */
  async execute(event: AutomationEvent, names: readonly string[]): Promise<string[]> {
    const failed: string[] = [];
    for (const name of names) {
      const workflow = this.workflows.get(name);
      if (!workflow) {
        // A rule referencing an unregistered workflow is a config bug;
        // surface it loudly rather than dropping the action.
        await this.dlq.send({
          event,
          workflow: name,
          attempts: 0,
          error: 'workflow not registered',
          failedAt: Date.now(),
        });
        this.metrics.increment('workflow.unknown', { workflow: name });
        failed.push(name);
        continue;
      }
      if (!(await this.runWithRetry(event, workflow))) failed.push(name);
    }
    return failed;
  }

  private async runWithRetry(event: AutomationEvent, workflow: Workflow): Promise<boolean> {
    for (let attempt = 1; ; attempt++) {
      const started = Date.now();
      try {
        await workflow.run({ event, attempt, metrics: this.metrics });
      } catch (err) {
        this.recordDuration(workflow, started);
        if (this.retry.shouldRetry(attempt, err)) {
          this.metrics.increment('workflow.retry', { workflow: workflow.name });
          await sleep(this.retry.delayFor(attempt));
          continue;
        }
        this.metrics.increment('workflow.failed', { workflow: workflow.name });
        await this.dlq.send({
          event,
          workflow: workflow.name,
          attempts: attempt,
          error: err instanceof Error ? `${err.name}: ${err.message}` : String(err),
          failedAt: Date.now(),
        });
        return false;
      }
      this.recordDuration(workflow, started);
      this.metrics.increment('workflow.succeeded', { workflow: workflow.name });
      return true;
    }
  }

  private recordDuration(workflow: Workflow, started: number): void {
    this.metrics.timing('workflow.duration_ms', Date.now() - started, {
      workflow: workflow.name,
    });
  }
}

// --------------------------------------------------------------------------- //
// Observability
// --------------------------------------------------------------------------- //

/** Cross-cutting hook — implement with StatsD, OpenTelemetry, or a test double. */
export interface MetricsLogger {
  increment(name: string, tags?: Readonly<Record<string, string>>): void;
  timing(name: string, valueMs: number, tags?: Readonly<Record<string, string>>): void;
}

/**
 * Default implementation: in-process counters, timings dropped.
 *
 * TODO(integration): forward to StatsD / Prometheus / OpenTelemetry.
 */
export class ConsoleMetrics implements MetricsLogger {
  readonly counters = new Map<string, number>();

  increment(name: string): void {
    this.counters.set(name, (this.counters.get(name) ?? 0) + 1);
  }

  timing(): void {
    /* no-op by default; wire a histogram here */
  }
}

// --------------------------------------------------------------------------- //
// Orchestration
// --------------------------------------------------------------------------- //

export interface AgentOptions {
  readonly maxConcurrency?: number;
  readonly leaseTtlMs?: number;
}

/**
 * Wires the layers together and owns the per-event lifecycle.
 *
 *     claim -> evaluate -> execute -> settle -> ack
 *
 * Ack/nack policy, deliberately:
 *
 * - A dead-lettered event is still acked: the DLQ owns it now, and
 *   redelivering would only re-run workflows that already failed.
 * - An event leased by another worker is nacked, never acked: if that worker
 *   crashes, the broker's redelivery is the only way back in.
 * - An unexpected agent-side fault releases the lease and nacks, so the
 *   broker can hand the event to a healthy replica — but only while the
 *   event is unsettled. Once the ledger records a verdict, releasing it would
 *   erase that verdict and re-run the workflows on redelivery.
 */
export class AutomationAgent {
  private readonly source: EventSource;
  private readonly evaluator: TriggerEvaluator;
  private readonly executor: WorkflowExecutor;
  private readonly store: IdempotencyStore;
  private readonly metrics: MetricsLogger;
  private readonly maxConcurrency: number;
  private readonly leaseTtlMs: number;

  constructor(
    source: EventSource,
    evaluator: TriggerEvaluator,
    executor: WorkflowExecutor,
    store: IdempotencyStore,
    metrics: MetricsLogger,
    options: AgentOptions = {},
  ) {
    this.source = source;
    this.evaluator = evaluator;
    this.executor = executor;
    this.store = store;
    this.metrics = metrics;
    this.maxConcurrency = options.maxConcurrency ?? 16;
    this.leaseTtlMs = options.leaseTtlMs ?? 300_000;
  }

  /** Consume until the source is drained, then drain in-flight work. */
  async run(): Promise<void> {
    const pending = new Set<Promise<void>>();
    try {
      for await (const event of this.source.events()) {
        const task: Promise<void> = this.handle(event).finally(() => {
          pending.delete(task);
        });
        pending.add(task);
        if (pending.size >= this.maxConcurrency) await Promise.race(pending);
      }
    } finally {
      await Promise.allSettled(pending);
    }
  }

  /** Never rejects — a rejection here would become an unhandled rejection. */
  private async handle(event: AutomationEvent): Promise<void> {
    this.metrics.increment('event.received', { source: event.source });
    let ownsLease = false; // true only between 'acquired' and settling
    try {
      const claim = await this.store.claim(event.id, this.leaseTtlMs);
      if (claim === 'settled') {
        this.metrics.increment('event.duplicate', { source: event.source });
        await this.source.ack(event);
        return;
      }
      if (claim === 'leased') {
        this.metrics.increment('event.leased', { source: event.source });
        await this.source.nack(event, new LeaseHeldError(event.id));
        return;
      }
      ownsLease = true;

      const names = this.evaluator.evaluate(event);
      if (names.length === 0) {
        this.metrics.increment('event.no_match', { source: event.source });
        await this.store.complete(event.id);
      } else {
        const failed = await this.executor.execute(event, names);
        await (failed.length > 0 ? this.store.fail(event.id) : this.store.complete(event.id));
      }
      ownsLease = false; // settled: the ledger now answers redeliveries

      await this.source.ack(event);
    } catch (err) {
      // Agent-side fault, not a workflow failure: give an unsettled lease
      // back so another replica can take the event. If release or nack fail
      // too, the lease TTL and the broker's redelivery timeout still apply.
      console.error(`agent fault handling event=${event.id}`, err);
      this.metrics.increment('event.agent_error', { source: event.source });
      if (ownsLease) await this.store.release(event.id).catch(() => {});
      await this.source.nack(event, err).catch(() => {});
    }
  }
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));
