/**
 * Behavioural tests for the automation agent's delivery guarantees — the
 * TypeScript twin of tests/test_agent.py. Run: node --test tests/agent.test.ts
 */

import assert from 'node:assert/strict';
import { mock, test } from 'node:test';

import {
  type AutomationEvent,
  AutomationAgent,
  type Claim,
  ConsoleMetrics,
  type DeadLetterQueue,
  type EventSource,
  InMemoryDeadLetterQueue,
  InMemoryIdempotencyStore,
  LeaseHeldError,
  PermanentError,
  RetryPolicy,
  TriggerEvaluator,
  type TriggerRule,
  type Workflow,
  WorkflowExecutor,
} from '../src/agent.ts';

// Dead-letters and agent faults are logged via console.error; keep test output clean.
mock.method(console, 'error', () => {});

const FAST_RETRY = new RetryPolicy({ maxAttempts: 3, baseDelayMs: 1, maxDelayMs: 10 });

class ListSource implements EventSource {
  readonly acked: string[] = [];
  readonly nacked: Array<[string, unknown]> = [];
  private readonly items: readonly AutomationEvent[];
  private failNextAck: boolean;

  constructor(items: readonly AutomationEvent[], { failFirstAck = false } = {}) {
    this.items = items;
    this.failNextAck = failFirstAck;
  }

  async *events(): AsyncIterable<AutomationEvent> {
    for (const e of this.items) yield e;
  }

  async ack(event: AutomationEvent): Promise<void> {
    if (this.failNextAck) {
      this.failNextAck = false;
      throw new Error('broker unreachable');
    }
    this.acked.push(event.id);
  }

  async nack(event: AutomationEvent, reason: unknown): Promise<void> {
    this.nacked.push([event.id, reason]);
  }
}

class Enrich implements Workflow {
  readonly name = 'enrich';
  runs = 0;
  async run(): Promise<void> {
    this.runs++;
  }
}

/** Times out once, then succeeds. */
class Flaky implements Workflow {
  readonly name = 'flaky';
  attempts = 0;
  async run(): Promise<void> {
    this.attempts++;
    if (this.attempts < 2) throw new Error('intel API timed out');
  }
}

class Quarantine implements Workflow {
  readonly name = 'quarantine';
  attempts = 0;
  async run(): Promise<void> {
    this.attempts++;
    throw new PermanentError('host decommissioned');
  }
}

const brokenDlq: DeadLetterQueue = {
  send: async () => {
    throw new Error('dlq unreachable');
  },
};

const RULES: TriggerRule[] = [
  { name: 'high-severity', matches: (e) => e.payload.severity === 'high', workflows: ['enrich'] },
  { name: 'siem-alerts', matches: (e) => e.source === 'siem', workflows: ['flaky'] },
  {
    name: 'critical-edr',
    matches: (e) => e.payload.severity === 'critical',
    workflows: ['quarantine'],
  },
];

const alert = (id: string, source = 'edr', severity = 'high'): AutomationEvent => ({
  id,
  source,
  type: 'alert.raised',
  payload: { severity },
  receivedAt: Date.now(),
});

interface RunOptions {
  store?: InMemoryIdempotencyStore;
  dlq?: DeadLetterQueue;
  source?: ListSource;
}

async function runAgent(events: AutomationEvent[], opts: RunOptions = {}) {
  const source = opts.source ?? new ListSource(events);
  const metrics = new ConsoleMetrics();
  const dlq = opts.dlq ?? new InMemoryDeadLetterQueue();
  const enrich = new Enrich();
  const flaky = new Flaky();
  const quarantine = new Quarantine();
  const executor = new WorkflowExecutor([enrich, flaky, quarantine], FAST_RETRY, dlq, metrics);
  // Serial, so a redelivery is handled after the original has settled.
  const agent = new AutomationAgent(
    source,
    new TriggerEvaluator(RULES),
    executor,
    opts.store ?? new InMemoryIdempotencyStore(),
    metrics,
    { maxConcurrency: 1 },
  );
  await agent.run();
  return { source, metrics, dlq, enrich, flaky, quarantine };
}

const letters = (dlq: DeadLetterQueue) => {
  assert.ok(dlq instanceof InMemoryDeadLetterQueue);
  return dlq.letters;
};

test('redelivered event runs its workflows once', async () => {
  const { source, metrics, enrich } = await runAgent([alert('A-1'), alert('A-1')]);

  assert.equal(enrich.runs, 1);
  assert.equal(metrics.counters.get('event.duplicate'), 1);
  assert.deepEqual(source.acked, ['A-1', 'A-1']); // duplicate is acked, not redelivered forever
});

test('transient failure is retried until it succeeds', async () => {
  const { source, metrics, dlq, flaky } = await runAgent([alert('B-1', 'siem', 'low')]);

  assert.equal(flaky.attempts, 2);
  assert.equal(metrics.counters.get('workflow.retry'), 1);
  assert.deepEqual(letters(dlq), []);
  assert.deepEqual(source.acked, ['B-1']);
});

test('permanent failure is dead-lettered without retry', async () => {
  const { source, dlq, quarantine } = await runAgent([alert('C-1', 'edr', 'critical')]);

  assert.equal(quarantine.attempts, 1);
  assert.deepEqual(
    letters(dlq).map((d) => [d.event.id, d.workflow, d.attempts]),
    [['C-1', 'quarantine', 1]],
  );
  // Acked: the DLQ owns it now, and redelivery would only fail again.
  assert.deepEqual(source.acked, ['C-1']);
  assert.deepEqual(source.nacked, []);
});

test('dead-lettered event is not re-run on redelivery', async () => {
  const { metrics, dlq, quarantine } = await runAgent([
    alert('C-1', 'edr', 'critical'),
    alert('C-1', 'edr', 'critical'),
  ]);

  assert.equal(quarantine.attempts, 1);
  assert.equal(letters(dlq).length, 1);
  assert.equal(metrics.counters.get('event.duplicate'), 1);
});

test('event matching no rule is settled and acked', async () => {
  const { source, metrics, enrich, flaky, quarantine } = await runAgent([
    alert('D-1', 'cloudtrail', 'info'),
  ]);

  assert.equal(metrics.counters.get('event.no_match'), 1);
  assert.equal(enrich.runs + flaky.attempts + quarantine.attempts, 0);
  assert.deepEqual(source.acked, ['D-1']);
});

test('event leased by another worker is nacked, not acked', async () => {
  const store = new InMemoryIdempotencyStore();
  assert.equal(await store.claim('A-1', 60_000), 'acquired'); // another worker

  const { source, enrich } = await runAgent([alert('A-1')], { store });

  assert.equal(enrich.runs, 0);
  assert.deepEqual(source.acked, []);
  assert.equal(source.nacked.length, 1);
  assert.equal(source.nacked[0]?.[0], 'A-1');
  assert.ok(source.nacked[0]?.[1] instanceof LeaseHeldError);
});

test('agent fault before settling releases the lease and nacks', async () => {
  const store = new InMemoryIdempotencyStore();

  const { source, metrics } = await runAgent([alert('C-1', 'edr', 'critical')], {
    store,
    dlq: brokenDlq,
  });

  assert.equal(metrics.counters.get('event.agent_error'), 1);
  assert.deepEqual(
    source.nacked.map(([id]) => id),
    ['C-1'],
  );
  // Lease was given back, so a healthy replica can take the redelivery.
  assert.equal(await store.claim('C-1', 60_000), 'acquired');
});

test('ack failure after settling keeps the verdict', async () => {
  const source = new ListSource([alert('A-1'), alert('A-1')], { failFirstAck: true });

  const { metrics, enrich } = await runAgent([], { source });

  // First ack failed -> nacked, but the verdict must survive: the redelivery
  // is recognised as a duplicate, not re-executed.
  assert.equal(enrich.runs, 1);
  assert.deepEqual(
    source.nacked.map(([id]) => id),
    ['A-1'],
  );
  assert.equal(metrics.counters.get('event.duplicate'), 1);
  assert.deepEqual(source.acked, ['A-1']);
});

test('expired lease can be reclaimed', async () => {
  const store = new InMemoryIdempotencyStore();
  await store.claim('A-1', 0); // holder crashed; lease lapses at once

  const claims: Claim[] = [await store.claim('A-1', 60_000), await store.claim('A-1', 60_000)];
  assert.deepEqual(claims, ['acquired', 'leased']);
});

test('retry delay is jittered within the capped ceiling', () => {
  const policy = new RetryPolicy({ baseDelayMs: 1000, maxDelayMs: 5000 });

  for (let attempt = 1; attempt < 10; attempt++) {
    const ceiling = Math.min(5000, 1000 * 2 ** (attempt - 1));
    for (let i = 0; i < 50; i++) {
      const delay = policy.delayFor(attempt);
      assert.ok(delay >= 0 && delay <= ceiling, `attempt ${attempt}: ${delay} > ${ceiling}`);
    }
  }
});
test('broken trigger rule is logged and skipped', async () => {
  const rules: TriggerRule[] = [
    { name: 'broken', matches: () => { throw new Error('rule is broken'); }, workflows: ['enrich'] },
    { name: 'high-severity', matches: (e) => e.payload.severity === 'high', workflows: ['enrich'] },
  ];
  const evaluator = new TriggerEvaluator(rules);
  const names = evaluator.evaluate(alert('A-1'));
  assert.ok(names.includes('enrich'));
});

test('unregistered workflow dead-letters', async () => {
  const rules: TriggerRule[] = [{ name: 'test', matches: () => true, workflows: ['missing'] }];
  const source = new ListSource([alert('A-1')]);
  const metrics = new ConsoleMetrics();
  const dlq = new InMemoryDeadLetterQueue();
  const executor = new WorkflowExecutor([new Enrich(), new Flaky(), new Quarantine()], new RetryPolicy({ maxAttempts: 3, baseDelayMs: 1, maxDelayMs: 10 }), dlq, metrics);
  const agent = new AutomationAgent(source, new TriggerEvaluator(rules), executor, new InMemoryIdempotencyStore(), metrics, { maxConcurrency: 1 });
  await agent.run();
  assert.equal(dlq.letters.length, 1);
  assert.equal(dlq.letters[0]?.workflow, 'missing');
  assert.ok(dlq.letters[0]?.error.includes('not registered'));
  assert.deepEqual(source.acked, ['A-1']);
});

