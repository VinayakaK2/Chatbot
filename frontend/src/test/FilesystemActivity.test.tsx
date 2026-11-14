import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ToolActivityItem } from '../components/activity/ToolActivityItem'
import { FileDiffViewer } from '../components/activity/FileDiffViewer'
import type { ToolActivity, FileDiff } from '../types/api'

describe('FilesystemActivity & Diff Rendering (OBS-01)', () => {
  it('renders read_file tool activity with file path pill, status, and collapsed output', () => {
    const readActivity: ToolActivity = {
      id: 'act-read-1',
      call_id: 'call-read-1',
      tool_name: 'read_file',
      operation: 'Read src/server.py',
      file_path: 'src/server.py',
      risk_category: 'read_only',
      permission_decision: 'allowed',
      status: 'completed',
      output: 'def start_server():\n    return True\n',
      timestamp: new Date().toISOString(),
    }

    const { container } = render(<ToolActivityItem activity={readActivity} />)

    expect(screen.getByText('read_file')).toBeDefined()
    expect(screen.getByText('Read src/server.py')).toBeDefined()
    expect(screen.getByText('server.py')).toBeDefined()
    expect(screen.getByText('completed')).toBeDefined()

    // Initially collapsed
    expect(screen.queryByText(/def start_server/)).toBeNull()

    // Expand to view output
    const header = container.querySelector('.tool-activity-header')
    expect(header).toBeDefined()
    fireEvent.click(header!)

    expect(screen.getByText(/def start_server/)).toBeDefined()
  })

  it('renders write_file activity with sanitized arguments and redacted content', () => {
    const writeActivity: ToolActivity = {
      id: 'act-write-1',
      call_id: 'call-write-1',
      tool_name: 'write_file',
      operation: 'Write .env.production',
      file_path: '.env.production',
      risk_category: 'workspace_write',
      permission_decision: 'allowed',
      status: 'completed',
      arguments: {
        path: '.env.production',
        content: '<redacted: 48 bytes>',
        content_bytes: 48,
      },
      output: 'Successfully wrote 48 bytes to .env.production',
      timestamp: new Date().toISOString(),
    }

    const { container } = render(<ToolActivityItem activity={writeActivity} />)

    expect(screen.getByText('write_file')).toBeDefined()
    expect(screen.getByText('.env.production')).toBeDefined()

    // Expand
    const header = container.querySelector('.tool-activity-header')
    fireEvent.click(header!)

    // Sanitized arguments must be visible in pre tag without leaking plaintext secrets
    expect(screen.getByText(/<redacted: 48 bytes>/)).toBeDefined()
    expect(screen.getByText(/Successfully wrote 48 bytes/)).toBeDefined()
  })

  it('renders edit_file activity with FileDiffViewer chunks and stat counters', () => {
    const sampleDiff: FileDiff = {
      file_path: 'src/version.py',
      added_lines: 1,
      removed_lines: 1,
      chunks: [
        {
          old_start: 1,
          old_lines: 3,
          new_start: 1,
          new_lines: 3,
          lines: [
            { type: 'context', content: 'APP_NAME = "Chatbot"', old_line_no: 1, new_line_no: 1 },
            { type: 'delete', content: '-VERSION = "0.1.0"', old_line_no: 2 },
            { type: 'add', content: '+VERSION = "0.2.0"', new_line_no: 2 },
            { type: 'context', content: 'AUTHOR = "Agent"', old_line_no: 3, new_line_no: 3 },
          ],
        },
      ],
    }

    const editActivity: ToolActivity = {
      id: 'act-edit-1',
      call_id: 'call-edit-1',
      tool_name: 'edit_file',
      operation: 'Edit src/version.py',
      file_path: 'src/version.py',
      risk_category: 'workspace_write',
      permission_decision: 'allowed',
      status: 'completed',
      diff: sampleDiff,
      timestamp: new Date().toISOString(),
    }

    const { container } = render(<ToolActivityItem activity={editActivity} />)

    // Expand
    const header = container.querySelector('.tool-activity-header')
    fireEvent.click(header!)

    // Verify diff contents
    expect(screen.getByText('+1')).toBeDefined()
    expect(screen.getByText('-1')).toBeDefined()
    expect(screen.getByText(/VERSION = "0.1.0"/)).toBeDefined()
    expect(screen.getByText(/VERSION = "0.2.0"/)).toBeDefined()
    expect(screen.getByText(/Chatbot/)).toBeDefined()
  })

  it('renders standalone FileDiffViewer with collapse and expand capability', () => {
    const diff: FileDiff = {
      file_path: 'config.json',
      added_lines: 2,
      removed_lines: 0,
      chunks: [
        {
          old_start: 1,
          old_lines: 1,
          new_start: 1,
          new_lines: 3,
          lines: [
            { type: 'context', content: '{', old_line_no: 1, new_line_no: 1 },
            { type: 'add', content: '  "enabled": true,', new_line_no: 2 },
            { type: 'add', content: '  "debug": false', new_line_no: 3 },
          ],
        },
      ],
    }

    const { container } = render(<FileDiffViewer diff={diff} initiallyExpanded={true} />)

    expect(screen.getByText('config.json')).toBeDefined()
    expect(screen.getByText('+2')).toBeDefined()
    expect(screen.getByText('-0')).toBeDefined()
    expect(screen.getByText(/"enabled": true/)).toBeDefined()

    // Collapse diff viewer
    const header = container.querySelector('.diff-viewer-header')
    expect(header).toBeDefined()
    fireEvent.click(header!)

    expect(screen.queryByText(/"enabled": true/)).toBeNull()
  })

  it('renders security policy denial and PathOutsideWorkspaceError states', () => {
    const deniedActivity: ToolActivity = {
      id: 'act-denied-1',
      call_id: 'call-denied-1',
      tool_name: 'read_file',
      operation: 'Read ../../etc/shadow',
      file_path: '../../etc/shadow',
      risk_category: 'read_only',
      permission_decision: 'denied',
      status: 'denied',
      error: 'PathOutsideWorkspaceError: Path resolves outside workspace boundary.',
      timestamp: new Date().toISOString(),
    }

    const { container } = render(<ToolActivityItem activity={deniedActivity} />)

    expect(screen.getByText('Policy Denied')).toBeDefined()
    expect(screen.getByText('denied')).toBeDefined()

    // Expand to view error alert
    const header = container.querySelector('.tool-activity-header')
    fireEvent.click(header!)

    expect(screen.getByRole('alert')).toBeDefined()
    expect(screen.getByText(/PathOutsideWorkspaceError/)).toBeDefined()
  })

  it('renders truncated output warning when output exceeds bounded limits', () => {
    const truncatedActivity: ToolActivity = {
      id: 'act-trunc-1',
      call_id: 'call-trunc-1',
      tool_name: 'list_directory',
      operation: 'List all repository files',
      risk_category: 'read_only',
      permission_decision: 'allowed',
      status: 'completed',
      output: 'Showing 500 of 1240 entries.\n... [Output truncated to max_directory_entries]',
      timestamp: new Date().toISOString(),
    }

    const { container } = render(<ToolActivityItem activity={truncatedActivity} />)

    const header = container.querySelector('.tool-activity-header')
    fireEvent.click(header!)

    expect(screen.getByText(/Output truncated/)).toBeDefined()
  })
})
