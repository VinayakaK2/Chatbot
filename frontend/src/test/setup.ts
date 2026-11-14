/**
 * Vitest DOM Test Setup File.
 * Mocks browser APIs like clipboard and scrollIntoView.
 */

// Mock Element.prototype.scrollIntoView
if (!window.HTMLElement.prototype.scrollIntoView) {
  window.HTMLElement.prototype.scrollIntoView = function () {}
}

// Mock navigator.clipboard
if (!navigator.clipboard) {
  Object.defineProperty(navigator, 'clipboard', {
    value: {
      writeText: async (_text: string) => Promise.resolve(),
      readText: async () => Promise.resolve(''),
    },
    writable: true,
  })
}
