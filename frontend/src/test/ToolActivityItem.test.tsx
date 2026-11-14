import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ToolActivityItem } from '../components/activity/ToolActivityItem'
import type { ToolActivity } from '../types/api'

describe('ToolActivityItem Component', () => {
  const mockActivity: ToolActivity = {
    id: 'act-1',
    call_id: 'call-1',
    tool_name: 'git_status',
    operation: 'Check clean repository worktree',
    risk_category: 'read_only',
    permission_decision: 'allowed',
    status: 'completed',
    command: 'git status',
    output: 'On branch main\nnothing to commit, working tree clean',
    timestamp: new Date().toISOString(),
  }

  it('renders tool name, operation, and status badge', () => {
    render(<ToolActivityItem activity={mockActivity} />)

    expect(screen.getByText('git_status')).toBeDefined()
    expect(screen.getByText('Check clean repository worktree')).toBeDefined()
    expect(screen.getByText('completed')).toBeDefined()
  })

  it('expands to reveal output when clicked', () => {
    const { container } = render(<ToolActivityItem activity={mockActivity} />)
    const header = container.querySelector('.tool-activity-header')
    expect(header).toBeDefined()

    // Initially output is collapsed
    expect(screen.queryByText(/nothing to commit/)).toBeNull()

    // Click to expand
    fireEvent.click(header!)
    expect(screen.getByText(/nothing to commit/)).toBeDefined()
  })

  it('renders "Approval Needed" when permission decision requires authorization', () => {
    const approvalActivity: ToolActivity = {
      ...mockActivity,
      permission_decision: 'approval_required',
      status: 'pending',
    }

    render(<ToolActivityItem activity={approvalActivity} />)
    expect(screen.getByText('Approval Needed')).toBeDefined()
  })
})
