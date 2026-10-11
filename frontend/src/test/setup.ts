import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// Explicit cleanup so timers/effects from `usePolling` are torn down between tests.
afterEach(() => {
  cleanup()
})
