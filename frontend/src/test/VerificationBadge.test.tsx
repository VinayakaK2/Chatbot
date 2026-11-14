import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { VerificationBadge } from '../components/activity/VerificationBadge'
import type { VerificationRecord } from '../types/api'

describe('VerificationBadge Component', () => {
  it('strictly displays "Verification Not Executed" when verification is absent or not run', () => {
    // Crucial rule: Task completion != Verification passed.
    // If no verification record exists, must NEVER show verified success.
    const { container } = render(<VerificationBadge verification={undefined} />)
    expect(screen.getByText('Verification Not Executed')).toBeDefined()
    expect(container.querySelector('.not-run')).toBeDefined()
  })

  it('renders verified passed state when verification.status is passed', () => {
    const record: VerificationRecord = {
      status: 'passed',
      summary: 'All 181 backend tests passed',
      timestamp: new Date().toISOString(),
      checks: [
        {
          name: 'pytest suite',
          command: 'pytest tests',
          status: 'passed',
          output: '181 passed in 2.40s',
        },
      ],
    }

    render(<VerificationBadge verification={record} />)
    expect(screen.getByText('Verified Passed')).toBeDefined()
    expect(screen.getByText('All 181 backend tests passed')).toBeDefined()
  })

  it('renders verification failed state when verification.status is failed', () => {
    const record: VerificationRecord = {
      status: 'failed',
      summary: '2 checks failed in regression test',
      timestamp: new Date().toISOString(),
      checks: [
        {
          name: 'type check',
          command: 'tsc -b',
          status: 'failed',
          output: 'error TS6133: unused variable',
        },
      ],
    }

    render(<VerificationBadge verification={record} />)
    expect(screen.getByText('Verification Failed')).toBeDefined()
    expect(screen.getByText('2 checks failed in regression test')).toBeDefined()
  })

  it('expands check details on click to show executed check details', () => {
    const record: VerificationRecord = {
      status: 'passed',
      summary: 'Code verification passed',
      timestamp: new Date().toISOString(),
      checks: [
        {
          name: 'Unit Tests',
          command: 'npm run test',
          status: 'passed',
          output: 'All tests passing',
        },
      ],
    }

    const { container } = render(<VerificationBadge verification={record} />)
    const header = container.querySelector('.verification-header')
    expect(header).toBeDefined()

    // Click to expand
    fireEvent.click(header!)
    expect(screen.getByText('Unit Tests')).toBeDefined()
    expect(screen.getByText('(npm run test)')).toBeDefined()
  })
})
