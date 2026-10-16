/**
 * Page selectors for practicesoftwaretesting.com
 * Single source of truth for element locators
 */

export const Selectors = {
  // Login page
  login: {
    emailInput: 'input#email',
    passwordInput: 'input#password',
    submitButton: 'button[type="submit"]',
    errorMessage: '.alert-danger',
  },

  // Products page
  products: {
    productCard: '.product-item',
    productTitle: '.product-title',
    productPrice: '.product-price',
    addToCartButton: '.btn-add-to-cart',
    productLink: 'a.product-link',
    filterByCategory: 'select#category',
  },

  // Cart page
  cart: {
    cartIcon: '.cart-icon',
    cartItems: '.cart-item',
    itemQuantity: 'input.quantity',
    removeButton: '.btn-remove',
    checkoutButton: '.btn-checkout',
    emptyCartMessage: '.empty-cart',
    cartTotal: '.cart-total',
  },

  // Checkout page
  checkout: {
    addressInput: 'input#address',
    cityInput: 'input#city',
    zipInput: 'input#zip',
    countrySelect: 'select#country',
    paymentMethod: 'select#payment_method',
    cardNumber: 'input#card-number',
    placeOrderButton: 'button.btn-place-order',
    orderConfirmation: '.order-success',
  },

  // Navigation
  nav: {
    logo: '.navbar-brand',
    homeLink: 'a[href="/"]',
    productsLink: 'a[href="/products"]',
    cartLink: '.cart-icon',
    profileIcon: '.profile-icon',
  },
};

export default Selectors;
