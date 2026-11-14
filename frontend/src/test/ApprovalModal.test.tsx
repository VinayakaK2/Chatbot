import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ApprovalModal } from '../components/overlays/ApprovalModal'
import type { PendingApproval } from '../types/api'

describe('ApprovalModal Component', () => {
  const mockApproval: PendingApproval = {
    call_id: 'call-bash-999',
    tool_name: 'bash',
    operation: 'Run database migration script',
    command: 'alembic upgrade head',
    working_dir: 'h:/chatbot',
    risk_category: 'command_execution',
    reason: 'Executing subprocess commands modifies local state and requires explicit operator approval.',
  }

  it('renders nothing when approval is null', () => {
    const { container } = render(
      <ApprovalModal
        approval={null}
        onApprove={() => {}}
        onReject={() => {}}
        onClose={() => {}}
      />
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders exact operation, command, risk category, and security reason', () => {
    render(
      <ApprovalModal
        approval={mockApproval}
        onApprove={() => {}}
        onReject={() => {}}
        onClose={() => {}}
      />
    )

    expect(screen.getByText('Permission Approval Required')).toBeDefined()
    expect(screen.getByText('Run database migration script')).toBeDefined()
    expect(screen.getByText('command_execution')).toBeDefined()
    expect(screen.getByText('alembic upgrade head')).toBeDefined()
    expect(screen.getByText('h:/chatbot')).toBeDefined()
    expect(
      screen.getByText(
        'Executing subprocess commands modifies local state and requires explicit operator approval.'
      )
    ).toBeDefined()
  })

  it('triggers onApprove with call_id when Authorize button is clicked', () => {
    const onApprove = vi.fn()
    const onReject = vi.fn()

    render(
      <ApprovalModal
        approval={mockApproval}
        onApprove={onApprove}
        onReject={onReject}
        onClose={() => {}}
      />
    )

    const authorizeBtn = screen.getByRole('button', { name: /Authorize & Execute/i })
    fireEvent.click(authorizeBtn)

    expect(onApprove).toHaveBeenCalledWith('call-bash-999')
    expect(onReject).not.toHaveBeenCalled()
  })

  it('triggers onReject with call_id when Reject button is clicked', () => {
    const onApprove = vi.fn()
    const onReject = vi.fn()

    render(
      <ApprovalModal
        approval={mockApproval}
        onApprove={onApprove}
        onReject={onReject}
        onClose={() => {}}
      />
    )

    const rejectBtn = screen.getByRole('button', { name: /Reject Operation/i })
    fireEvent.click(rejectBtn)

    expect(onReject).toHaveBeenCalledWith('call-bash-999', 'Rejected by operator')
    expect(onApprove).not.toHaveBeenCalled()
  })
})
