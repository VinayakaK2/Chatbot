import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { FileViewerPanel, type FileViewerTab } from '../components/layout/FileViewerPanel'

describe('FileViewerPanel Component', () => {
  const sampleTabs: FileViewerTab[] = [
    {
      id: 'tab-1',
      title: 'phase-4-test-evidence.md',
      path: 'docs/reports/phase-4-test-evidence.md',
      type: 'markdown',
      language: 'markdown',
      content: '# Forensic Evidence\n\n| Suite | Status |\n|:---|:---:|\n| Unit | Passed |',
    },
    {
      id: 'tab-2',
      title: 'main.py',
      path: 'main.py',
      type: 'code',
      language: 'python',
      content: 'def main():\n    return 0',
    },
  ]

  it('renders tab list, breadcrumbs, and segmented preview pill', () => {
    render(
      <FileViewerPanel
        isOpen={true}
        onClose={vi.fn()}
        initialTabs={sampleTabs}
        activeTabId="tab-1"
      />
    )

    expect(screen.getAllByText('phase-4-test-evidence.md').length).toBeGreaterThan(0)
    expect(screen.getByText('main.py')).toBeDefined()
    expect(screen.getByText('Preview')).toBeDefined()
    expect(screen.getByText('Raw')).toBeDefined()
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Forensic Evidence')
  })

  it('switches between Preview and Raw views for markdown files', () => {
    render(
      <FileViewerPanel
        isOpen={true}
        onClose={vi.fn()}
        initialTabs={sampleTabs}
        activeTabId="tab-1"
      />
    )

    // Initially in Preview mode - H1 heading rendered
    expect(screen.getByRole('heading', { level: 1 })).toBeDefined()

    // Switch to Raw mode
    const rawBtn = screen.getByText('Raw')
    fireEvent.click(rawBtn)

    // In Raw mode, markdown language badge or code block appears
    expect(screen.getByText('markdown')).toBeDefined()
  })

  it('switches tabs when tab title is clicked', () => {
    render(
      <FileViewerPanel
        isOpen={true}
        onClose={vi.fn()}
        initialTabs={sampleTabs}
        activeTabId="tab-1"
      />
    )

    // Click on second tab
    const codeTab = screen.getByText('main.py')
    fireEvent.click(codeTab)

    // Now code content should be displayed
    expect(screen.getByText('python')).toBeDefined()
  })

  it('triggers onClose when toggle side panel button is clicked', () => {
    const handleClose = vi.fn()
    render(
      <FileViewerPanel
        isOpen={true}
        onClose={handleClose}
        initialTabs={sampleTabs}
        activeTabId="tab-1"
      />
    )

    const toggleBtn = screen.getByLabelText('Toggle Side Panel')
    fireEvent.click(toggleBtn)
    expect(handleClose).toHaveBeenCalledTimes(1)
  })

  it('opens 3 dots menu and allows copying file content', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    })

    render(
      <FileViewerPanel
        isOpen={true}
        onClose={vi.fn()}
        initialTabs={sampleTabs}
        activeTabId="tab-1"
      />
    )

    const moreBtn = screen.getByLabelText('More Options')
    fireEvent.click(moreBtn)

    const copyBtn = screen.getByText('Copy File Content')
    fireEvent.click(copyBtn)

    expect(writeTextMock).toHaveBeenCalledWith(sampleTabs[0].content)
  })
})
