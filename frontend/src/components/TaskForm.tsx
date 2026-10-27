/**
 * Component providing user prompt submission with client-side bounds validation.
 */

import React, { useState } from 'react'
import type { TaskCreateRequest } from '../types/api'

interface TaskFormProps {
  onSubmit: (request: TaskCreateRequest) => Promise<void>
  isSubmitting: boolean
}

const MAX_PROMPT_CHARS = 32768

export const TaskForm: React.FC<TaskFormProps> = ({ onSubmit, isSubmitting }) => {
  const [prompt, setPrompt] = useState<string>('')
  const [maxTokens, setMaxTokens] = useState<number>(2048)
  const [temperature, setTemperature] = useState<number>(0.2)
  const [showOptions, setShowOptions] = useState<boolean>(false)
  const [validationError, setValidationError] = useState<string | null>(null)

  // Handle form submission after client-side sanitization and validation
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = prompt.trim()

    if (!trimmed) {
      setValidationError('Please enter a non-empty prompt.')
      return
    }

    if (trimmed.length > MAX_PROMPT_CHARS) {
      setValidationError(`Prompt exceeds maximum limit of ${MAX_PROMPT_CHARS.toLocaleString()} characters.`)
      return
    }

    setValidationError(null)
    await onSubmit({
      prompt: trimmed,
      max_tokens: maxTokens,
      temperature: temperature,
    })
  }

  // Handle prompt input change and clear validation warnings
  const handlePromptChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setPrompt(e.target.value)
    if (validationError) {
      setValidationError(null)
    }
  }

  const charCount = prompt.length
  const isTooLong = charCount > MAX_PROMPT_CHARS
  const isDisabled = isSubmitting || !prompt.trim() || isTooLong

  return (
    <section className="card" aria-label="Task Submission Form">
      <h2 className="card-title">Submit Prompt / Task</h2>
      <form onSubmit={handleSubmit} className="task-form">
        <div className="form-group">
          <label htmlFor="prompt-input" className="form-label">
            User Prompt / Instruction:
          </label>
          <textarea
            id="prompt-input"
            rows={5}
            value={prompt}
            onChange={handlePromptChange}
            disabled={isSubmitting}
            placeholder="e.g., Explain the difference between sync and async generators in Python."
            className="form-textarea"
            aria-describedby="prompt-counter"
          />
          <div className="form-footer">
            <span
              id="prompt-counter"
              className={`char-counter ${isTooLong ? 'text-danger' : ''}`}
            >
              {charCount.toLocaleString()} / {MAX_PROMPT_CHARS.toLocaleString()} characters
            </span>
          </div>
        </div>

        {validationError && (
          <div className="alert alert-warning" role="alert">
            {validationError}
          </div>
        )}

        <div className="options-toggle">
          <button
            type="button"
            onClick={() => setShowOptions(!showOptions)}
            className="btn btn-link"
            aria-expanded={showOptions}
          >
            {showOptions ? '▾ Hide Model Parameters' : '▸ Adjust Model Parameters (Optional)'}
          </button>
        </div>

        {showOptions && (
          <div className="options-panel">
            <div className="form-group">
              <label htmlFor="max-tokens" className="form-label">
                Max Output Tokens: {maxTokens}
              </label>
              <input
                id="max-tokens"
                type="range"
                min={256}
                max={8192}
                step={256}
                value={maxTokens}
                disabled={isSubmitting}
                onChange={(e) => setMaxTokens(Number(e.target.value))}
                className="form-slider"
              />
            </div>

            <div className="form-group">
              <label htmlFor="temperature" className="form-label">
                Temperature: {temperature.toFixed(2)}
              </label>
              <input
                id="temperature"
                type="range"
                min={0.0}
                max={1.5}
                step={0.05}
                value={temperature}
                disabled={isSubmitting}
                onChange={(e) => setTemperature(parseFloat(e.target.value))}
                className="form-slider"
              />
            </div>
          </div>
        )}

        <div className="form-actions">
          <button
            type="submit"
            disabled={isDisabled}
            className="btn btn-primary"
            aria-busy={isSubmitting}
          >
            {isSubmitting ? 'Processing Task...' : 'Submit to Model'}
          </button>
        </div>
      </form>
    </section>
  )
}
