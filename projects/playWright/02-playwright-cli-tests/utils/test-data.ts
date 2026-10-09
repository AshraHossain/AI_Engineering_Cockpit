/**
 * Test data for practicesoftwaretesting.com
 */

export interface TestUser {
  email: string;
  password: string;
  firstName: string;
  role: 'admin' | 'customer';
}

export interface TestProduct {
  id: string;
  name: string;
  price: number;
  category: string;
}

export const TestUsers: Record<string, TestUser> = {
  admin: {
    email: 'admin@practicesoftwaretesting.com',
    password: 'welcome01',
    firstName: 'John',
    role: 'admin',
  },
  customer1: {
    email: 'customer@practicesoftwaretesting.com',
    password: 'welcome01',
    firstName: 'Jane',
    role: 'customer',
  },
  customer2: {
    email: 'customer2@practicesoftwaretesting.com',
    password: 'welcome01',
    firstName: 'Jack',
    role: 'customer',
  },
  customer3: {
    email: 'customer3@practicesoftwaretesting.com',
    password: 'pass123',
    firstName: 'Bob',
    role: 'customer',
  },
};

export const TestProducts: TestProduct[] = [
  {
    id: '1',
    name: 'Leather Shoes',
    price: 99.99,
    category: 'footwear',
  },
  {
    id: '2',
    name: 'Running Shoes',
    price: 149.99,
    category: 'footwear',
  },
  {
    id: '3',
    name: 'T-Shirt',
    price: 29.99,
    category: 'clothing',
  },
];

export class CheckoutData {
  static validCheckout() {
    return {
      address: '123 Main St',
      city: 'San Francisco',
      zip: '94102',
      country: 'US',
      paymentMethod: 'credit_card',
    };
  }

  static invalidCheckout() {
    return {
      address: '',
      city: '',
      zip: '',
      country: '',
      paymentMethod: '',
    };
  }
}

export default { TestUsers, TestProducts, CheckoutData };
