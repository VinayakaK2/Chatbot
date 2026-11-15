/**
 * Desktop AI Application Composer Component.
 * Supports auto-expanding multiline input, file attachments, custom model selector popover,
 * reasoning level toggle, send/stop button, and keyboard shortcuts.
 */

import React, { useRef, useState, useEffect } from 'react'
import type { AttachedFile, WorkspaceInfo } from '../../types/api'
import { Dropdown } from '../common/Dropdown'
import {
  IconChevronDown,
  IconFile,
  IconFolder,
  IconPaperclip,
  IconSend,
  IconSparkles,
  IconSquare,
  IconX,
} from '../common/Icons'

export interface ComposerProps {
  onSendMessage: (text: string, attachments: AttachedFile[], model: string, reasoning: string) => void
  onStopGeneration?: () => void
  isGenerating?: boolean
  disabled?: boolean
  selectedModel?: string
  onSelectModel?: (model: string) => void
  placeholder?: string
  initialText?: string
  currentWorkspace?: WorkspaceInfo | null
  onOpenWorkspaceSelector?: () => void
}

const AVAILABLE_MODELS = [
  { id: 'antigravity/gemini-3.8-flash', label: 'Gemini 3.8 Flash' },
  { id: 'antigravity/gemini-3.8-flash-high', label: 'Gemini 3.8 Flash (High)' },
  { id: 'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ', label: 'Qwen 2.5 Coder 14B' },
  { id: 'gpt-4o', label: 'GPT-4o' },
  { id: 'claude-3-5-sonnet-20241022', label: 'Claude 3.5 Sonnet' },
]

const REASONING_LEVELS = [
  { id: 'low', label: 'Effort: Low (Fast)' },
  { id: 'medium', label: 'Effort: Medium (Balanced)' },
  { id: 'high', label: 'Effort: High (Thorough)' },
]

export const Composer: React.FC<ComposerProps> = ({
  onSendMessage,
  onStopGeneration,
  isGenerating = false,
  disabled = false,
  selectedModel = 'antigravity/gemini-3.8-flash',
  onSelectModel,
  placeholder,
  initialText,
  currentWorkspace,
  onOpenWorkspaceSelector,
}) => {
  const [text, setText] = useState(initialText || '')
  const [attachments, setAttachments] = useState<AttachedFile[]>([])
  const [activeModel, setActiveModel] = useState(selectedModel)
  const [reasoning, setReasoning] = useState('high')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setActiveModel(selectedModel)
  }, [selectedModel])

  useEffect(() => {
    if (initialText !== undefined) {
      setText(initialText)
    }
  }, [initialText])

  // Auto-resize textarea height smoothly up to 200px
  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value)
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`
    }
  }

  // Handle Enter to submit, Shift+Enter for newline
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleSubmit = () => {
    const trimmed = text.trim()
    if (!trimmed && attachments.length === 0) return
    if (isGenerating || disabled) return

    onSendMessage(trimmed, attachments, activeModel, reasoning)
    setText('')
    setAttachments([])
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  // File Attachment handling — reads file text or image base64 data URL into attachment content
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return

    const fileList = Array.from(files)
    const newAttachments: AttachedFile[] = await Promise.all(
      fileList.map(async (file) => {
        let content: string | undefined = undefined
        const isImage = file.type.startsWith('image/')

        if (isImage) {
          try {
            content = await new Promise<string>((resolve, reject) => {
              const reader = new FileReader()
              reader.onload = () => resolve(reader.result as string)
              reader.onerror = reject
              reader.readAsDataURL(file)
            })
          } catch (err) {
            console.warn('Could not read image file as data URL:', err)
            content = `[Failed to read image ${file.name}]`
          }
        } else if (file.size > 2 * 1024 * 1024) {
          content = `[File ${file.name} is too large (${Math.round(file.size / 1024)} KB > 2MB) to embed directly into prompt context]`
        } else {
          try {
            content = await file.text()
          } catch (err) {
            console.warn('Could not read file text:', err)
            content = `[Could not read file ${file.name}]`
          }
        }

        return {
          id: `${file.name}-${Date.now()}-${Math.random()}`,
          name: file.name,
          size: file.size,
          type: file.type || 'text/plain',
          content,
        }
      })
    )

    setAttachments((prev) => [...prev, ...newAttachments])
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const removeAttachment = (id: string) => {
    setAttachments((prev) => prev.filter((att) => att.id !== id))
  }

  const currentModelObj = AVAILABLE_MODELS.find((m) => m.id === activeModel)
  const displayModelName = currentModelObj
    ? currentModelObj.label
    : activeModel.split('/').pop() || activeModel

  const modelDropdownItems = AVAILABLE_MODELS.map((m) => ({
    id: m.id,
    label: m.label,
    onClick: () => {
      setActiveModel(m.id)
      if (onSelectModel) onSelectModel(m.id)
    },
  }))

  const reasoningDropdownItems = REASONING_LEVELS.map((r) => ({
    id: r.id,
    label: r.label,
    onClick: () => setReasoning(r.id),
  }))

  const canSubmit = (text.trim().length > 0 || attachments.length > 0) && !disabled && !isGenerating

  return (
    <div className="composer-card" role="region" aria-label="Task Composer">
      {/* Attachment chips preview */}
      {attachments.length > 0 && (
        <div className="composer-attachments-row">
          {attachments.map((file) => (
            <div key={file.id} className="composer-attachment-chip">
              <IconFile size={13} color="var(--accent-primary)" />
              <span className="chip-name">{file.name}</span>
              <span className="chip-size">({Math.round(file.size / 1024)} KB)</span>
              <button
                type="button"
                className="chip-remove-btn"
                onClick={() => removeAttachment(file.id)}
                aria-label={`Remove ${file.name}`}
              >
                <IconX size={11} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Multiline textarea */}
      <textarea
        ref={textareaRef}
        value={text}
        onChange={handleInput}
        onKeyDown={handleKeyDown}
        placeholder={placeholder || "Ask anything, @ to mention, / for actions"}
        className="composer-textarea"
        rows={1}
        disabled={disabled || isGenerating}
        aria-label="Task prompt input"
      />

      {/* Bottom Controls Bar inside the Composer */}
      <div className="composer-bottom-bar">
        <div className="composer-controls-left">
          {/* File Attachment Button */}
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileChange}
            style={{ display: 'none' }}
            multiple
          />
          <button
            type="button"
            className="composer-btn-icon"
            onClick={() => fileInputRef.current?.click()}
            title="Attach files or diffs"
            aria-label="Attach files"
            disabled={disabled || isGenerating}
          >
            <IconPaperclip size={15} />
          </button>

          {/* Model Selector Popover */}
          <Dropdown
            trigger={
              <button
                type="button"
                className="composer-pill-btn"
                title="Select model"
                disabled={isGenerating}
                aria-label="Select model"
              >
                <IconSparkles size={13} color="var(--accent-primary)" />
                <span className="pill-text">
                  {displayModelName}
                </span>
                <IconChevronDown size={11} color="var(--text-muted)" />
              </button>
            }
            items={modelDropdownItems}
            align="left"
          />

          {/* Reasoning Effort Selector */}
          <Dropdown
            trigger={
              <button
                type="button"
                className="composer-pill-btn"
                title="Reasoning effort"
                disabled={isGenerating}
                aria-label="Reasoning effort"
              >
                <span className="pill-text">Effort: {reasoning}</span>
                <IconChevronDown size={11} color="var(--text-muted)" />
              </button>
            }
            items={reasoningDropdownItems}
            align="left"
          />

          {/* Phase 4 Workspace Scope Indicator & Quick Switcher */}
          {currentWorkspace && (
            <button
              type="button"
              className="composer-pill-btn composer-workspace-pill"
              onClick={onOpenWorkspaceSelector}
              title={`Active Workspace: ${currentWorkspace.root_path} (Click to switch folder)`}
              aria-label="Active workspace folder"
            >
              <IconFolder size={12} color="var(--accent-primary)" />
              <span className="pill-text">{currentWorkspace.name}</span>
            </button>
          )}
        </div>

        <div className="composer-controls-right">
          {isGenerating ? (
            <button
              type="button"
              className="composer-stop-btn"
              onClick={onStopGeneration}
              title="Stop execution"
              aria-label="Stop generation"
            >
              <IconSquare size={13} />
              <span>Stop</span>
            </button>
          ) : (
            <button
              type="button"
              className={`composer-send-btn ${canSubmit ? 'is-active active' : ''}`}
              onClick={handleSubmit}
              disabled={!canSubmit}
              title="Send task (Enter)"
              aria-label="Send prompt"
            >
              <IconSend size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
