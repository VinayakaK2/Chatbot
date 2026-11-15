/**
 * Antigravity-Style File & Diff Viewer Split Panel.
 * Supports multi-tab browsing, Markdown live rendering (Preview / Raw),
 * Git diff inspection, workspace file exploration, and interactive PowerShell terminal.
 */

import React, { useEffect, useState, useRef, useCallback } from 'react'
import type { FileDiff, VerificationRecord, WorkspaceFileItem } from '../../types/api'
import { CodeBlock } from '../chat/CodeBlock'
import { MarkdownRenderer } from '../chat/MarkdownRenderer'
import { FileDiffViewer } from '../activity/FileDiffViewer'
import { VerificationBadge } from '../activity/VerificationBadge'
import { TerminalView } from './TerminalView'
import { apiClient } from '../../services/api-client'
import {
  IconCheck,
  IconCopy,
  IconDiff,
  IconDocument,
  IconFile,
  IconFileCode,
  IconMaximize,
  IconMoreVertical,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconSidebar,
  IconTerminal,
  IconX,
} from '../common/Icons'

export interface FileViewerTab {
  id: string
  title: string
  path: string
  type: 'markdown' | 'code' | 'diff' | 'terminal' | 'verification'
  content: string
  language?: string
  diff?: FileDiff | null
  verification?: VerificationRecord | null
  terminalOutput?: { command: string; output: string } | null
}

export interface FileViewerPanelProps {
  isOpen: boolean
  onClose: () => void
  activeTabId?: string
  initialTabs?: FileViewerTab[]
  onOpenDiff?: () => void
  workspaceName?: string
  workspaceRoot?: string
}

export const FileViewerPanel: React.FC<FileViewerPanelProps> = ({
  isOpen,
  onClose,
  activeTabId: externalActiveTabId,
  initialTabs,
  onOpenDiff,
  workspaceName = 'workspace',
  workspaceRoot = 'h:\\chatbot',
}) => {
  const [tabs, setTabs] = useState<FileViewerTab[]>(() => {
    if (initialTabs && initialTabs.length > 0) return initialTabs
    return [
      {
        id: 'powershell.exe',
        title: 'powershell.exe',
        path: 'powershell.exe',
        type: 'terminal',
        language: 'shell',
        content: '',
      },
    ]
  })

  const [activeTabId, setActiveTabId] = useState<string>(
    externalActiveTabId || (tabs[0] ? tabs[0].id : '')
  )
  const [viewMode, setViewMode] = useState<'preview' | 'raw'>('preview')
  const [copied, setCopied] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [isPlusMenuOpen, setIsPlusMenuOpen] = useState(false)
  const [isFilePickerOpen, setIsFilePickerOpen] = useState(false)
  const [isMoreMenuOpen, setIsMoreMenuOpen] = useState(false)
  const [fileSearchQuery, setFileSearchQuery] = useState('')
  const [workspaceFiles, setWorkspaceFiles] = useState<WorkspaceFileItem[]>([])
  const [isLoadingFiles, setIsLoadingFiles] = useState(false)
  const [isLoadingFile, setIsLoadingFile] = useState(false)
  const [isFitToScreen, setIsFitToScreen] = useState<boolean>(() => {
    try {
      const saved = localStorage.getItem('chatbot_file_viewer_fit_to_screen')
      if (saved !== null) return saved === 'true'
    } catch {
      // Ignore localStorage access failures
    }
    return true
  })

  const plusMenuRef = useRef<HTMLDivElement>(null)
  const filePickerRef = useRef<HTMLDivElement>(null)
  const moreMenuRef = useRef<HTMLDivElement>(null)

  const handleToggleFitToScreen = () => {
    setIsFitToScreen((prev) => {
      const next = !prev
      try {
        localStorage.setItem('chatbot_file_viewer_fit_to_screen', String(next))
      } catch {
        // Ignore localStorage access failures
      }
      return next
    })
  }

  // Draggable width state
  const MIN_PANEL_WIDTH = 360
  const getInitialWidth = () => {
    if (typeof window === 'undefined') return 560
    try {
      const saved = localStorage.getItem('chatbot_preview_panel_width')
      if (saved) {
        const parsed = Number(saved)
        const maxW = Math.max(window.innerWidth - 360, MIN_PANEL_WIDTH)
        if (!isNaN(parsed) && parsed >= MIN_PANEL_WIDTH && parsed <= maxW) {
          return parsed
        }
      }
    } catch {
      // Ignore localStorage access failures
    }
    return Math.min(Math.max(Math.round(window.innerWidth * 0.48), 480), 820)
  }

  const [panelWidth, setPanelWidth] = useState<number>(getInitialWidth)
  const [isDragging, setIsDragging] = useState(false)
  const isDraggingRef = useRef(false)

  const handleResetWidth = useCallback(() => {
    const defaultW = Math.min(Math.max(Math.round(window.innerWidth * 0.48), 480), 820)
    setPanelWidth(defaultW)
    try {
      localStorage.setItem('chatbot_preview_panel_width', String(defaultW))
    } catch {}
  }, [])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setIsDragging(true)
    isDraggingRef.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  const handleTouchStart = useCallback(() => {
    setIsDragging(true)
    isDraggingRef.current = true
  }, [])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      setPanelWidth((prev) => {
        const next = Math.min(prev + 24, window.innerWidth - 360)
        try { localStorage.setItem('chatbot_preview_panel_width', String(next)) } catch {}
        return next
      })
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      setPanelWidth((prev) => {
        const next = Math.max(prev - 24, MIN_PANEL_WIDTH)
        try { localStorage.setItem('chatbot_preview_panel_width', String(next)) } catch {}
        return next
      })
    } else if (e.key === 'Enter') {
      handleResetWidth()
    }
  }, [handleResetWidth])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDraggingRef.current) return
      const maxW = Math.max(window.innerWidth - 360, MIN_PANEL_WIDTH)
      const newWidth = window.innerWidth - e.clientX
      const clamped = Math.min(Math.max(newWidth, MIN_PANEL_WIDTH), maxW)
      setPanelWidth(clamped)
    }

    const handleMouseUp = () => {
      if (isDraggingRef.current) {
        isDraggingRef.current = false
        setIsDragging(false)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        setPanelWidth((curr) => {
          try {
            localStorage.setItem('chatbot_preview_panel_width', String(curr))
          } catch {}
          return curr
        })
      }
    }

    const handleTouchMove = (e: TouchEvent) => {
      if (!isDraggingRef.current || !e.touches[0]) return
      const maxW = Math.max(window.innerWidth - 360, MIN_PANEL_WIDTH)
      const newWidth = window.innerWidth - e.touches[0].clientX
      const clamped = Math.min(Math.max(newWidth, MIN_PANEL_WIDTH), maxW)
      setPanelWidth(clamped)
    }

    const handleTouchEnd = () => {
      if (isDraggingRef.current) {
        isDraggingRef.current = false
        setIsDragging(false)
        setPanelWidth((curr) => {
          try {
            localStorage.setItem('chatbot_preview_panel_width', String(curr))
          } catch {}
          return curr
        })
      }
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    window.addEventListener('touchmove', handleTouchMove)
    window.addEventListener('touchend', handleTouchEnd)

    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
      window.removeEventListener('touchmove', handleTouchMove)
      window.removeEventListener('touchend', handleTouchEnd)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [])

  // Sync external tab request
  useEffect(() => {
    if (externalActiveTabId) {
      setActiveTabId(externalActiveTabId)
    }
  }, [externalActiveTabId])

  // If initialTabs changes from parent (e.g. artifact/diff/file passed in)
  useEffect(() => {
    if (initialTabs && initialTabs.length > 0) {
      setTabs((prev) => {
        const merged = [...prev]
        for (const newTab of initialTabs) {
          const existingIdx = merged.findIndex((t) => t.id === newTab.id || t.path === newTab.path)
          if (existingIdx >= 0) {
            merged[existingIdx] = newTab
          } else {
            merged.push(newTab)
          }
        }
        return merged
      })
      if (initialTabs[0]) {
        setActiveTabId(initialTabs[0].id)
      }
    }
  }, [initialTabs])

  // Fetch content for tabs that need content loaded
  useEffect(() => {
    const activeTab = tabs.find((t) => t.id === activeTabId)
    if (
      activeTab &&
      (activeTab.type === 'markdown' || activeTab.type === 'code') &&
      activeTab.content.startsWith('# Loading') &&
      typeof apiClient?.getFileContent === 'function'
    ) {
      setIsLoadingFile(true)
      apiClient
        .getFileContent(activeTab.path)
        .then((res) => {
          setTabs((prev) =>
            prev.map((t) =>
              t.id === activeTab.id
                ? {
                    ...t,
                    content: res.content,
                    title: res.name,
                    language: res.language,
                    type: res.is_markdown ? 'markdown' : 'code',
                  }
                : t
            )
          )
        })
        .catch(() => {
          setTabs((prev) =>
            prev.map((t) =>
              t.id === activeTab.id
                ? {
                    ...t,
                    content: `// Error: Unable to load ${activeTab.path}\n// File not found in workspace: ${workspaceRoot}`,
                  }
                : t
            )
          )
        })
        .finally(() => setIsLoadingFile(false))
    }
  }, [activeTabId, tabs, workspaceRoot])

  const handleReloadActiveFile = () => {
    const activeTab = tabs.find((t) => t.id === activeTabId)
    if (!activeTab || activeTab.type === 'terminal' || activeTab.type === 'diff') return
    setIsLoadingFile(true)
    apiClient
      .getFileContent(activeTab.path)
      .then((res) => {
        setTabs((prev) =>
          prev.map((t) =>
            t.id === activeTab.id
              ? {
                  ...t,
                  content: res.content,
                  language: res.language,
                }
              : t
          )
        )
      })
      .catch((err) => {
        console.warn('Failed to reload file content', err)
      })
      .finally(() => setIsLoadingFile(false))
  }

  // Fetch workspace files when opening file picker
  const handleOpenWorkspaceFilePicker = async () => {
    setIsPlusMenuOpen(false)
    setIsFilePickerOpen(true)
    setIsLoadingFiles(true)
    try {
      const files = await apiClient.getAvailableDocuments(true)
      setWorkspaceFiles(files)
    } catch {
      setWorkspaceFiles([])
    } finally {
      setIsLoadingFiles(false)
    }
  }

  // Handle selecting a file from workspace file picker
  const handleSelectWorkspaceFile = (file: WorkspaceFileItem) => {
    setIsFilePickerOpen(false)
    setFileSearchQuery('')
    const existing = tabs.find((t) => t.path === file.path)
    if (existing) {
      setActiveTabId(existing.id)
      return
    }

    const newTabId = `file-${file.path}`
    const isMd = file.is_markdown || file.name.endsWith('.md')
    const lang = file.name.endsWith('.py')
      ? 'python'
      : file.name.endsWith('.ts') || file.name.endsWith('.tsx')
      ? 'typescript'
      : file.name.endsWith('.js')
      ? 'javascript'
      : file.name.endsWith('.json')
      ? 'json'
      : isMd
      ? 'markdown'
      : 'text'

    const newTab: FileViewerTab = {
      id: newTabId,
      title: file.name,
      path: file.path,
      type: isMd ? 'markdown' : 'code',
      language: lang,
      content: '# Loading file content...',
    }

    setTabs((prev) => [...prev, newTab])
    setActiveTabId(newTabId)
  }

  // Handle creating a new terminal tab
  const handleOpenNewTerminal = () => {
    setIsPlusMenuOpen(false)
    const termCount = tabs.filter((t) => t.type === 'terminal').length
    const termId = termCount === 0 ? 'powershell.exe' : `powershell-${termCount + 1}.exe`
    const termTitle = termCount === 0 ? 'powershell.exe' : `powershell (${termCount + 1}).exe`

    const existing = tabs.find((t) => t.id === termId)
    if (existing) {
      setActiveTabId(existing.id)
      return
    }

    const newTermTab: FileViewerTab = {
      id: termId,
      title: termTitle,
      path: 'powershell.exe',
      type: 'terminal',
      language: 'shell',
      content: '',
    }

    setTabs((prev) => [...prev, newTermTab])
    setActiveTabId(termId)
  }

  // Close menus on click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (plusMenuRef.current && !plusMenuRef.current.contains(e.target as Node)) {
        setIsPlusMenuOpen(false)
      }
      if (filePickerRef.current && !filePickerRef.current.contains(e.target as Node)) {
        setIsFilePickerOpen(false)
      }
      if (moreMenuRef.current && !moreMenuRef.current.contains(e.target as Node)) {
        setIsMoreMenuOpen(false)
      }
    }
    if (isPlusMenuOpen || isFilePickerOpen || isMoreMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [isPlusMenuOpen, isFilePickerOpen, isMoreMenuOpen])

  if (!isOpen) return null

  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0]

  const handleCloseTab = (e: React.MouseEvent, tabId: string) => {
    e.stopPropagation()
    const nextTabs = tabs.filter((t) => t.id !== tabId)
    setTabs(nextTabs)
    if (activeTabId === tabId) {
      if (nextTabs.length > 0) {
        setActiveTabId(nextTabs[nextTabs.length - 1].id)
      } else {
        onClose()
      }
    }
  }

  const handleCopyContent = async () => {
    if (!activeTab) return
    const textToCopy =
      activeTab.type === 'diff' && activeTab.diff
        ? (activeTab.diff as any).patch || activeTab.diff.new_content || ''
        : activeTab.content
    try {
      await navigator.clipboard.writeText(textToCopy)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error('Failed to copy', err)
    }
  }

  // Filtered files for search
  const filteredFiles = workspaceFiles.filter((f) => {
    if (!fileSearchQuery) return true
    const q = fileSearchQuery.toLowerCase()
    return f.name.toLowerCase().includes(q) || f.path.toLowerCase().includes(q)
  })

  // Generate breadcrumb items
  const breadcrumbParts = activeTab
    ? activeTab.path
        .replace(/\\/g, '/')
        .split('/')
        .filter(Boolean)
    : []

  return (
    <aside
      className={`file-viewer-split-pane ${isFullscreen ? 'is-fullscreen' : ''} ${
        isDragging ? 'is-resizing' : ''
      }`}
      style={!isFullscreen ? { width: `${panelWidth}px` } : undefined}
      role="complementary"
      aria-label="Files and Git Diff Viewer Panel"
    >
      {/* Draggable Divider Handle */}
      {!isFullscreen && (
        <div
          className={`panel-resize-handle ${isDragging ? 'is-dragging' : ''}`}
          onMouseDown={handleMouseDown}
          onTouchStart={handleTouchStart}
          onDoubleClick={handleResetWidth}
          onKeyDown={handleKeyDown}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize panel divider"
          aria-valuenow={panelWidth}
          aria-valuemin={MIN_PANEL_WIDTH}
          aria-valuemax={typeof window !== 'undefined' ? window.innerWidth - 360 : 1200}
          tabIndex={0}
          title="Drag to resize panel (Double-click to reset, Left/Right arrow keys)"
        />
      )}

      {/* 1. Tabs Bar */}
      <div className="file-viewer-tabs-bar">
        <div className="file-viewer-tabs-scroll">
          {tabs.map((tab) => {
            const isActive = tab.id === activeTab?.id
            return (
              <div
                key={tab.id}
                role="tab"
                tabIndex={0}
                aria-selected={isActive}
                className={`file-viewer-tab ${isActive ? 'is-active' : ''}`}
                onClick={() => setActiveTabId(tab.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    setActiveTabId(tab.id)
                  }
                }}
              >
                {/* Tab Icon Badge */}
                <span className="file-tab-badge">
                  {tab.language === 'python' || tab.path.endsWith('.py') ? (
                    <span className="badge-python-glyph" title="Python">🐍</span>
                  ) : tab.type === 'markdown' ? (
                    <span className="badge-md-glyph">M↓</span>
                  ) : tab.type === 'diff' ? (
                    <IconDiff size={13} color="var(--color-primary)" />
                  ) : tab.type === 'terminal' ? (
                    <IconTerminal size={13} />
                  ) : (
                    <IconFileCode size={13} />
                  )}
                </span>

                <span className="file-tab-title" title={tab.path || tab.title}>
                  {tab.title}
                </span>

                <button
                  type="button"
                  className="file-tab-close-btn"
                  onClick={(e) => handleCloseTab(e, tab.id)}
                  title={`Close ${tab.title}`}
                  aria-label={`Close ${tab.title}`}
                >
                  <IconX size={12} />
                </button>
              </div>
            )
          })}

          {/* Plus (+) Add Tab Button with Dropdown Menu */}
          <div className="file-tab-add-container" ref={plusMenuRef}>
            <button
              type="button"
              className="file-tab-add-btn"
              onClick={() => setIsPlusMenuOpen((prev) => !prev)}
              title="Open File or New Terminal (+)"
              aria-label="Add Tab Options"
            >
              <IconPlus size={14} />
            </button>

            {/* Dropdown: Open File... / New Terminal */}
            {isPlusMenuOpen && (
              <div className="file-plus-menu-dropdown" role="menu">
                <button
                  type="button"
                  className="file-plus-menu-item"
                  onClick={handleOpenWorkspaceFilePicker}
                  role="menuitem"
                >
                  <IconFileCode size={13} />
                  <span>Open File...</span>
                </button>
                <button
                  type="button"
                  className="file-plus-menu-item"
                  onClick={handleOpenNewTerminal}
                  role="menuitem"
                >
                  <IconTerminal size={13} />
                  <span>New Terminal</span>
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Top Right Panel Controls */}
        <div className="file-viewer-top-actions">
          <button
            type="button"
            className="panel-tool-btn"
            onClick={() => setIsFullscreen((prev) => !prev)}
            title={isFullscreen ? 'Exit Fullscreen' : 'Toggle Fullscreen'}
            aria-label="Toggle Fullscreen"
          >
            <IconMaximize size={14} />
          </button>

          <button
            type="button"
            className="panel-tool-btn panel-tool-toggle-btn"
            onClick={onClose}
            title="Toggle Side Panel (Close)"
            aria-label="Toggle Side Panel"
          >
            <IconSidebar size={16} />
          </button>
        </div>
      </div>

      {/* Quick Workspace File Picker Overlay */}
      {isFilePickerOpen && (
        <div className="workspace-file-picker-overlay" ref={filePickerRef} role="dialog" aria-label="Select file to open">
          <div className="workspace-file-picker-header">
            <span>Open File in {workspaceName}</span>
            <button
              type="button"
              className="breadcrumb-icon-btn"
              onClick={() => setIsFilePickerOpen(false)}
              aria-label="Close file picker"
            >
              <IconX size={13} />
            </button>
          </div>
          <div className="workspace-file-picker-search-bar">
            <IconSearch size={14} color="var(--text-muted)" />
            <input
              type="text"
              className="workspace-file-picker-input"
              value={fileSearchQuery}
              onChange={(e) => setFileSearchQuery(e.target.value)}
              placeholder="Search files by name (e.g. tests, app.tsx)..."
              autoFocus
            />
          </div>
          <div className="workspace-file-picker-list">
            {isLoadingFiles ? (
              <div className="file-viewer-loading">
                <span className="inline-loader-spinner" />
                <span>Loading workspace files...</span>
              </div>
            ) : filteredFiles.length === 0 ? (
              <div className="workspace-file-picker-empty">
                No matching files found in {workspaceName}
              </div>
            ) : (
              filteredFiles.map((file) => (
                <button
                  key={file.path}
                  type="button"
                  className="workspace-file-picker-item"
                  onClick={() => handleSelectWorkspaceFile(file)}
                >
                  {file.name.endsWith('.py') ? (
                    <span className="badge-python-glyph" style={{ fontSize: 13 }}>🐍</span>
                  ) : file.is_markdown ? (
                    <span className="badge-md-glyph" style={{ fontSize: 10 }}>M↓</span>
                  ) : (
                    <IconFile size={13} color="var(--text-secondary)" />
                  )}
                  <span className="workspace-file-picker-item-name">{file.name}</span>
                  <span className="workspace-file-picker-item-path">{file.path}</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}

      {/* 2. Breadcrumb & Action Toolbar */}
      <div className="file-viewer-breadcrumb-bar">
        {/* Left: Dynamic Breadcrumbs rooted in active workspace folder */}
        <div className="file-breadcrumb-trail">
          <span className="breadcrumb-root">{workspaceName}</span>
          {breadcrumbParts.map((part, idx) => (
            <React.Fragment key={`${part}-${idx}`}>
              <span className="breadcrumb-separator">&rsaquo;</span>
              <span
                className={`breadcrumb-part ${
                  idx === breadcrumbParts.length - 1 ? 'is-filename' : ''
                }`}
              >
                {idx === breadcrumbParts.length - 1 && (activeTab?.language === 'python' || activeTab?.path.endsWith('.py')) && (
                  <span className="breadcrumb-icon-badge python" aria-hidden="true">🐍</span>
                )}
                {idx === breadcrumbParts.length - 1 && activeTab?.type === 'markdown' && (
                  <span className="breadcrumb-icon-badge" aria-hidden="true">M↓</span>
                )}
                {part}
              </span>
            </React.Fragment>
          ))}
        </div>

        {/* Right: Segmented Preview / Raw Pill & Actions */}
        <div className="file-breadcrumb-actions">
          {activeTab?.type === 'markdown' && (
            <div className="segmented-preview-pill">
              <button
                type="button"
                className={`segmented-pill-btn ${viewMode === 'preview' ? 'is-active' : ''}`}
                onClick={() => setViewMode('preview')}
              >
                Preview
              </button>
              <button
                type="button"
                className={`segmented-pill-btn ${viewMode === 'raw' ? 'is-active' : ''}`}
                onClick={() => setViewMode('raw')}
              >
                Raw
              </button>
            </div>
          )}

          {/* Three Dots More Menu */}
          <div className="file-more-menu-container" ref={moreMenuRef}>
            <button
              type="button"
              className={`breadcrumb-icon-btn ${isMoreMenuOpen ? 'is-active' : ''}`}
              onClick={() => setIsMoreMenuOpen((prev) => !prev)}
              title="More Options"
              aria-label="More Options"
            >
              <IconMoreVertical size={13} />
            </button>

            {isMoreMenuOpen && (
              <div className="file-more-dropdown" role="menu">
                <button
                  type="button"
                  className="file-more-dropdown-item"
                  onClick={() => {
                    handleCopyContent()
                    setIsMoreMenuOpen(false)
                  }}
                  role="menuitem"
                >
                  {copied ? (
                    <>
                      <IconCheck size={13} color="var(--color-success)" />
                      <span className="file-more-item-label is-success">Copied to Clipboard!</span>
                    </>
                  ) : (
                    <>
                      <IconCopy size={13} />
                      <span className="file-more-item-label">Copy File Content</span>
                    </>
                  )}
                </button>

                <button
                  type="button"
                  className="file-more-dropdown-item"
                  onClick={() => {
                    handleToggleFitToScreen()
                    setIsMoreMenuOpen(false)
                  }}
                  role="menuitem"
                >
                  <IconMaximize size={13} />
                  <span className="file-more-item-label">
                    {isFitToScreen ? 'Fit to Screen (Wrap: ON)' : 'Fit to Screen (Wrap: OFF)'}
                  </span>
                  {isFitToScreen && <IconCheck size={12} color="var(--color-file-text, #60a5fa)" />}
                </button>

                <button
                  type="button"
                  className="file-more-dropdown-item"
                  onClick={() => {
                    handleReloadActiveFile()
                    setIsMoreMenuOpen(false)
                  }}
                  role="menuitem"
                >
                  <IconRefresh size={13} />
                  <span className="file-more-item-label">Reload File</span>
                </button>
              </div>
            )}
          </div>

          {/* Paper Icon (Diff review) */}
          <button
            type="button"
            className="breadcrumb-icon-btn"
            onClick={onOpenDiff}
            title="Review Git Diff"
            aria-label="Review Git Diff"
          >
            <IconDocument size={13} />
          </button>

          {/* Refresh active file content */}
          <button
            type="button"
            className="breadcrumb-icon-btn"
            onClick={handleReloadActiveFile}
            title="Reload file"
            aria-label="Reload file"
          >
            <IconRefresh size={13} />
          </button>
        </div>
      </div>

      {/* 3. Panel Body: TerminalView OR Markdown Renderer OR CodeBlock OR DiffViewer */}
      <div className={`file-viewer-body ${isFitToScreen ? 'is-fit-to-screen' : ''}`}>
        {isLoadingFile ? (
          <div className="file-viewer-loading">
            <span className="inline-loader-spinner" />
            <span>Loading document...</span>
          </div>
        ) : activeTab?.type === 'terminal' ? (
          <TerminalView
            workingDir={workspaceRoot}
            initialCommand={activeTab.terminalOutput?.command}
            initialOutput={activeTab.terminalOutput?.output || activeTab.content}
          />
        ) : activeTab?.type === 'diff' ? (
          <div className="file-viewer-diff-wrap">
            {activeTab.diff ? (
              <FileDiffViewer diff={activeTab.diff} initiallyExpanded={true} />
            ) : (
              <pre className="raw-diff-pre">{activeTab.content}</pre>
            )}
          </div>
        ) : activeTab?.type === 'verification' && activeTab.verification ? (
          <div className="file-viewer-verification-wrap">
            <VerificationBadge verification={activeTab.verification} />
          </div>
        ) : activeTab?.type === 'markdown' && viewMode === 'preview' ? (
          <div className="file-viewer-markdown-content">
            <MarkdownRenderer content={activeTab.content} />
          </div>
        ) : (
          <div className={`file-viewer-raw-content ${isFitToScreen ? 'is-fit-to-screen' : ''}`}>
            <CodeBlock
              code={activeTab?.content || ''}
              language={activeTab?.language || (activeTab?.type === 'markdown' ? 'markdown' : 'text')}
              fitToScreen={isFitToScreen}
            />
          </div>
        )}
      </div>
    </aside>
  )
}
