/**
 * Streaming-Safe Markdown Renderer.
 * Parses markdown blocks (code fences, headings, tables, lists, quotes, inline formatting)
 * without external library overhead, safely handling incomplete streaming tokens.
 */

import React from 'react'
import { CodeBlock } from './CodeBlock'

export interface MarkdownRendererProps {
  content: string
  isStreaming?: boolean
}

// Inline token rendering for bold, italic, inline code, and links
function renderInlineFormatting(text: string): React.ReactNode[] {
  // Regex matches: inline code, bold (**, __), italic (*, _), strikethrough (~~), markdown link [text](url)
  const inlineRegex =
    /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|~~[^~]+~~|\[([^\]]+)\]\(([^)]+)\))/g

  const nodes: React.ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = inlineRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index))
    }

    const token = match[0]
    if (token.startsWith('`') && token.endsWith('`')) {
      nodes.push(
        <code key={match.index} className="inline-code">
          {token.slice(1, -1)}
        </code>
      )
    } else if (
      (token.startsWith('**') && token.endsWith('**')) ||
      (token.startsWith('__') && token.endsWith('__'))
    ) {
      nodes.push(
        <strong key={match.index} className="font-semibold">
          {token.slice(2, -2)}
        </strong>
      )
    } else if (
      (token.startsWith('*') && token.endsWith('*')) ||
      (token.startsWith('_') && token.endsWith('_'))
    ) {
      nodes.push(
        <em key={match.index} className="italic">
          {token.slice(1, -1)}
        </em>
      )
    } else if (token.startsWith('~~') && token.endsWith('~~')) {
      nodes.push(
        <del key={match.index} className="line-through">
          {token.slice(2, -2)}
        </del>
      )
    } else if (match[2] && match[3]) {
      // Markdown link
      nodes.push(
        <a
          key={match.index}
          href={match[3]}
          target="_blank"
          rel="noopener noreferrer"
          className="markdown-link"
        >
          {match[2]}
        </a>
      )
    }

    lastIndex = inlineRegex.lastIndex
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }

  return nodes.length > 0 ? nodes : [text]
}

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({
  content,
  isStreaming = false,
}) => {
  if (!content) {
    return isStreaming ? <span className="streaming-cursor" aria-hidden="true" /> : null
  }

  // Parse reasoning tags <think>...</think> only if explicitly emitted by reasoning models
  let thoughtText: string | null = null
  let textToParse = content

  const thinkMatch = content.match(/<think>([\s\S]*?)(?:<\/think>|$)/i)
  if (thinkMatch) {
    thoughtText = thinkMatch[1].trim()
    textToParse = content.replace(/<think>[\s\S]*?(?:<\/think>|$)/i, '').trim()
  }

  const lines = textToParse ? textToParse.replace(/\r\n/g, '\n').split('\n') : []
  const blocks: React.ReactNode[] = []

  let inCodeBlock = false
  let codeLang = ''
  let codeBuffer: string[] = []

  let inTable = false
  let tableHeader: string[] = []
  let tableRows: string[][] = []

  let inList = false
  let listType: 'ul' | 'ol' = 'ul'
  let listItems: { text: string; indent: number }[] = []

  const flushList = (key: string) => {
    if (!inList) return
    if (listType === 'ul') {
      blocks.push(
        <ul key={key} className="markdown-ul">
          {listItems.map((item, idx) => (
            <li
              key={idx}
              className={`markdown-li ${item.indent > 0 ? 'markdown-li-nested' : ''}`}
              style={item.indent > 0 ? { paddingLeft: `${Math.min(item.indent * 16, 48)}px` } : undefined}
            >
              {renderInlineFormatting(item.text)}
            </li>
          ))}
        </ul>
      )
    } else {
      blocks.push(
        <ol key={key} className="markdown-ol">
          {listItems.map((item, idx) => (
            <li
              key={idx}
              className={`markdown-li ${item.indent > 0 ? 'markdown-li-nested' : ''}`}
              style={item.indent > 0 ? { paddingLeft: `${Math.min(item.indent * 16, 48)}px` } : undefined}
            >
              {renderInlineFormatting(item.text)}
            </li>
          ))}
        </ol>
      )
    }
    inList = false
    listItems = []
  }

  const flushTable = (key: string) => {
    if (!inTable) return
    blocks.push(
      <div key={key} className="table-wrapper">
        <table className="markdown-table">
          {tableHeader.length > 0 && (
            <thead>
              <tr>
                {tableHeader.map((cell, idx) => (
                  <th key={idx}>{renderInlineFormatting(cell.trim())}</th>
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {tableRows.map((row, rowIdx) => (
              <tr key={rowIdx}>
                {row.map((cell, colIdx) => (
                  <td key={colIdx}>{renderInlineFormatting(cell.trim())}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
    inTable = false
    tableHeader = []
    tableRows = []
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    // Code Fence check
    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        // Close code block
        blocks.push(
          <CodeBlock
            key={`code-${i}`}
            code={codeBuffer.join('\n')}
            language={codeLang}
          />
        )
        inCodeBlock = false
        codeLang = ''
        codeBuffer = []
      } else {
        // Start code block
        flushList(`list-before-code-${i}`)
        flushTable(`table-before-code-${i}`)
        inCodeBlock = true
        codeLang = line.trim().replace(/^```/, '').trim()
        codeBuffer = []
      }
      continue
    }

    if (inCodeBlock) {
      codeBuffer.push(line)
      continue
    }

    // Suppress robotic greeting headers, status banner boilerplate, or boot status lines
    const trimmed = line.trim()
    if (
      /^(>?\s*Status:\s*(Online|Ready|Idle|Active|Operational|Available).*)$/i.test(trimmed) ||
      /^(#{1,6}\s+)?(Greetings!?|Ready for Development|System Initialized.*|Session Initialized.*|Capabilities & Operations.*)$/i.test(trimmed) ||
      /^I am configured and ready to assist.*$/i.test(trimmed) ||
      /^System initialized and operating according to.*$/i.test(trimmed) ||
      /^Ready to assist\.\s*Please provide your repository details.*$/i.test(trimmed)
    ) {
      continue
    }

    // Table check: pipes
    if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
      flushList(`list-before-table-${i}`)
      const cells = line
        .trim()
        .slice(1, -1)
        .split('|')

      // Divider row like |---|---|
      const isDivider = cells.every((c) => /^\s*:?-+:?\s*$/.test(c))
      if (isDivider) {
        continue
      }

      if (!inTable) {
        inTable = true
        tableHeader = cells
        tableRows = []
      } else {
        tableRows.push(cells)
      }
      continue
    } else if (inTable) {
      flushTable(`table-${i}`)
    }

    // Unordered List (- or * or •)
    const ulMatch = line.match(/^(\s*)[-*•]\s+(.+)$/)
    if (ulMatch) {
      if (!inList || listType !== 'ul') {
        flushList(`list-prev-${i}`)
        inList = true
        listType = 'ul'
      }
      const indentLevel = Math.floor(ulMatch[1].length / 2)
      listItems.push({ text: ulMatch[2], indent: indentLevel })
      continue
    }

    // Ordered List (1. 2.)
    const olMatch = line.match(/^(\s*)\d+\.\s+(.+)$/)
    if (olMatch) {
      if (!inList || listType !== 'ol') {
        flushList(`list-prev-${i}`)
        inList = true
        listType = 'ol'
      }
      const indentLevel = Math.floor(olMatch[1].length / 2)
      listItems.push({ text: olMatch[2], indent: indentLevel })
      continue
    }

    if (inList) {
      flushList(`list-${i}`)
    }

    // Headings (H1 to H6)
    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/)
    if (headingMatch) {
      const level = headingMatch[1].length
      const title = headingMatch[2]
      if (level === 1) {
        blocks.push(
          <h1 key={`h1-${i}`} className="markdown-h1">
            {renderInlineFormatting(title)}
          </h1>
        )
      } else if (level === 2) {
        blocks.push(
          <h2 key={`h2-${i}`} className="markdown-h2">
            {renderInlineFormatting(title)}
          </h2>
        )
      } else if (level === 3) {
        blocks.push(
          <h3 key={`h3-${i}`} className="markdown-h3">
            {renderInlineFormatting(title)}
          </h3>
        )
      } else {
        blocks.push(
          <h4 key={`h4-${i}`} className="markdown-h4">
            {renderInlineFormatting(title)}
          </h4>
        )
      }
      continue
    }

    // Blockquote
    if (line.startsWith('> ')) {
      blocks.push(
        <blockquote key={`quote-${i}`} className="markdown-blockquote">
          {renderInlineFormatting(line.slice(2))}
        </blockquote>
      )
      continue
    }

    // Horizontal Rule
    if (/^(\*\*\*|---|___)$/.test(line.trim())) {
      blocks.push(<hr key={`hr-${i}`} className="markdown-hr" />)
      continue
    }

    // Paragraph or empty line
    if (line.trim() === '') {
      continue
    }

    blocks.push(
      <p key={`p-${i}`} className="markdown-p">
        {renderInlineFormatting(line)}
      </p>
    )
  }

  // Handle trailing unclosed code block safely during streaming
  if (inCodeBlock) {
    blocks.push(
      <CodeBlock
        key="code-unclosed"
        code={codeBuffer.join('\n')}
        language={codeLang}
      />
    )
  }

  // Flush any pending lists or tables at EOF
  if (inList) {
    flushList('list-final')
  }
  if (inTable) {
    flushTable('table-final')
  }

  return (
    <div className="markdown-body">
      {thoughtText && (
        <details className="thought-disclosure">
          <summary className="thought-summary">
            <span>Thought</span>
          </summary>
          <div className="thought-body">{thoughtText}</div>
        </details>
      )}
      {blocks.length === 0 && !isStreaming && content.trim() !== '' ? (
        <p className="markdown-p">Hello! How can I help you today?</p>
      ) : (
        blocks
      )}
      {isStreaming && <span className="streaming-cursor" aria-hidden="true" />}
    </div>
  )
}
