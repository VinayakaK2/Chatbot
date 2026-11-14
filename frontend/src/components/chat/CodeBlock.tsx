/**
 * Production CodeBlock Component with syntax tokenization, line numbers, and copy action.
 * Pure TypeScript tokenizer with zero external runtime dependencies.
 */

import React, { useState } from 'react'
import { IconCheck, IconCopy } from '../common/Icons'

export interface CodeBlockProps {
  code: string
  language?: string
  showLineNumbers?: boolean
}

// Tokenize line for lightweight, reliable syntax highlighting without external heavy libs
function tokenizeLine(line: string, lang: string): React.ReactNode[] {
  const normalizedLang = (lang || '').toLowerCase()

  // Diff line coloring
  if (normalizedLang === 'diff' || line.startsWith('+') || line.startsWith('-')) {
    if (line.startsWith('+') && !line.startsWith('+++')) {
      return [<span key="diff-add" className="token-diff-add">{line}</span>]
    }
    if (line.startsWith('-') && !line.startsWith('---')) {
      return [<span key="diff-del" className="token-diff-del">{line}</span>]
    }
    if (line.startsWith('@@')) {
      return [<span key="diff-hdr" className="token-diff-hdr">{line}</span>]
    }
  }

  // General syntax highlighter regex
  // Match: comments, strings, keywords, numbers
  const regex =
    /(\/\/[^\n]*|#[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b(?:def|class|return|import|from|as|if|else|elif|for|while|try|except|finally|raise|with|async|await|const|let|var|function|type|interface|export|default|null|true|false|undefined|None|True|False)\b|\b\d+(?:\.\d+)?\b)/g

  const nodes: React.ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = regex.exec(line)) !== null) {
    const textBefore = line.slice(lastIndex, match.index)
    if (textBefore) {
      nodes.push(textBefore)
    }

    const token = match[0]
    let tokenClass = 'token-plain'

    if (token.startsWith('//') || token.startsWith('#')) {
      tokenClass = 'token-comment'
    } else if (
      token.startsWith('"') ||
      token.startsWith("'") ||
      token.startsWith('`')
    ) {
      tokenClass = 'token-string'
    } else if (/^\d/.test(token)) {
      tokenClass = 'token-number'
    } else {
      tokenClass = 'token-keyword'
    }

    nodes.push(
      <span key={match.index} className={tokenClass}>
        {token}
      </span>
    )
    lastIndex = regex.lastIndex
  }

  const remainder = line.slice(lastIndex)
  if (remainder) {
    nodes.push(remainder)
  }

  return nodes
}

export const CodeBlock: React.FC<CodeBlockProps> = ({
  code,
  language = 'text',
  showLineNumbers = true,
}) => {
  const [copied, setCopied] = useState(false)
  const lines = code.replace(/\r\n/g, '\n').split('\n')

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error('Failed to copy to clipboard', err)
    }
  }

  return (
    <div className="code-block-container" role="region" aria-label={`Code block: ${language}`}>
      <div className="code-block-header">
        <span className="code-block-language">{language || 'text'}</span>
        <button
          type="button"
          className="code-block-copy-btn"
          onClick={handleCopy}
          aria-label="Copy code to clipboard"
        >
          {copied ? (
            <>
              <IconCheck size={14} color="var(--color-success)" />
              <span className="copy-label copied">Copied!</span>
            </>
          ) : (
            <>
              <IconCopy size={14} />
              <span className="copy-label">Copy</span>
            </>
          )}
        </button>
      </div>

      <div className="code-block-scroll">
        <pre className="code-block-pre">
          <code>
            {lines.map((line, idx) => (
              <div key={idx} className="code-line">
                {showLineNumbers && (
                  <span className="code-line-number" aria-hidden="true">
                    {idx + 1}
                  </span>
                )}
                <span className="code-line-content">
                  {tokenizeLine(line, language)}
                  {line === '' ? ' ' : ''}
                </span>
              </div>
            ))}
          </code>
        </pre>
      </div>
    </div>
  )
}
