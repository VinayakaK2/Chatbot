/**
 * Interactive PowerShell Terminal View.
 * Bounded to active project workspace with real local command execution.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react'
import { apiClient } from '../../services/api-client'
import { IconCheck } from '../common/Icons'

export interface TerminalEntry {
  id: string
  command: string
  workingDir: string
  stdout: string
  stderr: string
  exitCode: number
  durationMs?: number
  timestamp: string
}

export interface TerminalViewProps {
  workingDir?: string
  initialCommand?: string
  initialOutput?: string
  onCommandRun?: (cmd: string) => void
}

export const TerminalView: React.FC<TerminalViewProps> = ({
  workingDir = 'h:\\chatbot',
  initialCommand,
  initialOutput,
  onCommandRun,
}) => {
  const [entries, setEntries] = useState<TerminalEntry[]>(() => {
    if (initialCommand || initialOutput) {
      return [
        {
          id: `init-${Date.now()}`,
          command: initialCommand || 'Get-Location',
          workingDir,
          stdout: initialOutput || '',
          stderr: '',
          exitCode: 0,
          timestamp: new Date().toLocaleTimeString(),
        },
      ]
    }
    return []
  })

  const [inputCommand, setInputCommand] = useState('')
  const [isExecuting, setIsExecuting] = useState(false)
  const [history, setHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState<number>(-1)

  const terminalEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Auto-scroll to bottom on output change
  const scrollToBottom = useCallback(() => {
    if (terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [entries, isExecuting, scrollToBottom])

  // Focus input on mount or when clicking inside terminal
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Sync external initialCommand / initialOutput changes if new one arrives
  useEffect(() => {
    if (initialCommand && initialOutput) {
      setEntries((prev) => {
        const exists = prev.some((e) => e.command === initialCommand && e.stdout === initialOutput)
        if (exists) return prev
        return [
          ...prev,
          {
            id: `entry-${Date.now()}`,
            command: initialCommand,
            workingDir,
            stdout: initialOutput,
            stderr: '',
            exitCode: 0,
            timestamp: new Date().toLocaleTimeString(),
          },
        ]
      })
    }
  }, [initialCommand, initialOutput, workingDir])

  const handleExecute = async (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    const trimmed = inputCommand.trim()
    if (!trimmed || isExecuting) return

    // Add to history
    setHistory((prev) => [...prev, trimmed])
    setHistoryIndex(-1)
    setInputCommand('')
    setIsExecuting(true)

    const tempId = `run-${Date.now()}`
    try {
      const res = await apiClient.executeCommand(trimmed, workingDir)
      setEntries((prev) => [
        ...prev,
        {
          id: tempId,
          command: trimmed,
          workingDir: res.working_dir || workingDir,
          stdout: res.stdout,
          stderr: res.stderr,
          exitCode: res.exit_code,
          durationMs: res.duration_ms,
          timestamp: new Date().toLocaleTimeString(),
        },
      ])
      if (onCommandRun) onCommandRun(trimmed)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      setEntries((prev) => [
        ...prev,
        {
          id: tempId,
          command: trimmed,
          workingDir,
          stdout: '',
          stderr: msg,
          exitCode: 1,
          timestamp: new Date().toLocaleTimeString(),
        },
      ])
    } finally {
      setIsExecuting(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (history.length === 0) return
      const nextIdx = historyIndex === -1 ? history.length - 1 : Math.max(0, historyIndex - 1)
      setHistoryIndex(nextIdx)
      setInputCommand(history[nextIdx] || '')
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (historyIndex === -1) return
      const nextIdx = historyIndex + 1
      if (nextIdx >= history.length) {
        setHistoryIndex(-1)
        setInputCommand('')
      } else {
        setHistoryIndex(nextIdx)
        setInputCommand(history[nextIdx] || '')
      }
    } else if (e.key === 'c' && e.ctrlKey) {
      setInputCommand('')
      setHistoryIndex(-1)
    } else if (e.key === 'l' && e.ctrlKey) {
      e.preventDefault()
      setEntries([])
    }
  }

  const handleClear = () => {
    setEntries([])
    inputRef.current?.focus()
  }

  return (
    <div
      className="interactive-terminal-container"
      onClick={() => inputRef.current?.focus()}
      role="region"
      aria-label="Local Terminal Console"
    >
      {/* Terminal Toolbar */}
      <div className="terminal-header-bar">
        <div className="terminal-header-title">
          <span className="terminal-dot green" />
          <span>powershell.exe</span>
          <span className="terminal-header-path">{workingDir}</span>
        </div>
        <div className="terminal-header-actions">
          <button
            type="button"
            className="terminal-tool-btn"
            onClick={handleClear}
            title="Clear Terminal (Ctrl+L)"
            aria-label="Clear Terminal"
          >
            Clear
          </button>
        </div>
      </div>

      {/* Terminal Scroll Body */}
      <div className="terminal-scroll-area">
        <div className="terminal-welcome-banner">
          <div>Windows PowerShell (Workspace Execution Bounded)</div>
          <div>Copyright (C) Microsoft Corporation. All rights reserved.</div>
          <div className="terminal-banner-sub">
            Working directory: <code className="terminal-code-dir">{workingDir}</code>
          </div>
        </div>

        {/* Command Output History */}
        {entries.map((entry) => (
          <div key={entry.id} className="terminal-entry-block">
            <div className="terminal-prompt-line">
              <span className="terminal-prompt-prefix">PS</span>
              <span className="terminal-prompt-cwd">{entry.workingDir}&gt;</span>
              <span className="terminal-prompt-command">{entry.command}</span>
              {entry.durationMs !== undefined && (
                <span className="terminal-entry-duration">
                  ({(entry.durationMs / 1000).toFixed(1)}s)
                </span>
              )}
              {entry.exitCode === 0 ? (
                <span className="terminal-status-ok" title="Exit code: 0">
                  <IconCheck size={11} color="var(--color-success, #22c55e)" />
                </span>
              ) : (
                <span className="terminal-status-err" title={`Exit code: ${entry.exitCode}`}>
                  ✕ {entry.exitCode}
                </span>
              )}
            </div>

            {entry.stdout && (
              <pre className="terminal-output-stdout">{entry.stdout}</pre>
            )}

            {entry.stderr && (
              <pre className="terminal-output-stderr">{entry.stderr}</pre>
            )}
          </div>
        ))}

        {/* Live Running State */}
        {isExecuting && (
          <div className="terminal-running-line">
            <span className="inline-loader-spinner" />
            <span className="terminal-running-text">Executing command...</span>
          </div>
        )}

        {/* Active Command Input Line */}
        <form className="terminal-input-form" onSubmit={handleExecute}>
          <div className="terminal-input-prompt">
            <span className="terminal-prompt-prefix">PS</span>
            <span className="terminal-prompt-cwd">{workingDir}&gt;</span>
          </div>
          <input
            ref={inputRef}
            type="text"
            className="terminal-command-input"
            value={inputCommand}
            onChange={(e) => setInputCommand(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isExecuting}
            placeholder={isExecuting ? 'Running command...' : 'Type command (e.g. dir, git status, pytest)...'}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
          />
        </form>

        <div ref={terminalEndRef} />
      </div>
    </div>
  )
}
