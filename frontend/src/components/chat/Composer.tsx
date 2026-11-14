/**
 * Desktop AI Application Composer Component.
 * Supports auto-expanding multiline input, file attachments, custom model selector popover,
 * reasoning level toggle, send/stop button, and keyboard shortcuts.
 */

import React, { useRef, useState, useEffect } from 'react'
import type { AttachedFile } from '../../types/api'
import { Dropdown } from '../common/Dropdown'
import {
  IconChevronDown,
  IconFile,
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
}

const AVAILABLE_MODELS = [
  { id: 'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ', label: 'Qwen 2.5 Coder 14B (vLLM)', description: 'Local / Colab vLLM Backend (Recommended)' },
  { id: 'antigravity/gemini-3.8-flash-high', label: 'Gemini 3.8 Flash (High)', description: 'Fast multimodal reasoning' },
  { id: 'claude-3-5-sonnet-20241022', label: 'Claude 3.5 Sonnet', description: 'Advanced code intelligence' },
  { id: 'gpt-4o', label: 'GPT-4o', description: 'OpenAI multi-domain model' },
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
  selectedModel = 'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ',
  onSelectModel,
  placeholder,
  initialText,
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

  // File Attachment handling
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return

    const newAttachments: AttachedFile[] = Array.from(files).map((file) => ({
      id: `${file.name}-${Date.now()}-${Math.random()}`,
      name: file.name,
      size: file.size,
      type: file.type || 'text/plain',
    }))

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
    ? currentModelObj.label.split(' (')[0]
    : activeModel.split('/').pop() || activeModel

  const modelDropdownItems = AVAILABLE_MODELS.map((m) => ({
    id: m.id,
    label: m.label,
    description: m.description,
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
