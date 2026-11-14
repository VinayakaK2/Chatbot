import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MarkdownRenderer } from '../components/chat/MarkdownRenderer'

describe('MarkdownRenderer Component', () => {
  it('renders headings and paragraph content correctly', () => {
    const markdown = '# Workspace Architecture\n\nThis is a production runtime system.'
    render(<MarkdownRenderer content={markdown} />)

    const heading = screen.getByRole('heading', { level: 1 })
    expect(heading).toBeDefined()
    expect(heading.textContent).toBe('Workspace Architecture')
    expect(screen.getByText('This is a production runtime system.')).toBeDefined()
  })

  it('renders unordered and ordered lists', () => {
    const markdown = '- First item\n- Second item\n\n1. Numbered one\n2. Numbered two'
    render(<MarkdownRenderer content={markdown} />)

    expect(screen.getByText('First item')).toBeDefined()
    expect(screen.getByText('Second item')).toBeDefined()
    expect(screen.getByText('Numbered one')).toBeDefined()
    expect(screen.getByText('Numbered two')).toBeDefined()
  })

  it('renders code blocks with code language and line numbers', () => {
    const code = '```typescript\nconst runtimeVersion = "0.1.0"\nconsole.log(runtimeVersion)\n```'
    const { container } = render(<MarkdownRenderer content={code} />)

    const codeBlock = container.querySelector('.code-block-container')
    expect(codeBlock).toBeDefined()
    expect(container.querySelector('.code-block-language')?.textContent).toBe('typescript')
    expect(codeBlock?.textContent).toContain('runtimeVersion')
  })

  it('gracefully handles unclosed streaming code fences without crashing', () => {
    // Simulates live model streaming mid-chunk where closing ``` has not yet arrived
    const streamingCode = 'Here is the implementation:\n```python\ndef execute_task():\n    return True'
    const { container } = render(<MarkdownRenderer content={streamingCode} isStreaming={true} />)

    const codeBlock = container.querySelector('.code-block-container')
    expect(codeBlock).toBeDefined()
    expect(codeBlock?.textContent).toContain('execute_task')
  })

  it('renders streaming cursor indicator when isStreaming is true', () => {
    const { container } = render(
      <MarkdownRenderer content="Generating response..." isStreaming={true} />
    )
    const cursor = container.querySelector('.streaming-cursor')
    expect(cursor).toBeDefined()
  })

  it('renders markdown tables with header and body rows', () => {
    const tableMd = '| Layer | Responsibility |\n| --- | --- |\n| Surface | User Interface |\n| Core | Agent Loop |'
    render(<MarkdownRenderer content={tableMd} />)

    expect(screen.getByText('Layer')).toBeDefined()
    expect(screen.getByText('Responsibility')).toBeDefined()
    expect(screen.getByText('Surface')).toBeDefined()
    expect(screen.getByText('User Interface')).toBeDefined()
    expect(screen.getByText('Core')).toBeDefined()
    expect(screen.getByText('Agent Loop')).toBeDefined()
  })

  it('renders links, bold, italics, and blockquotes cleanly', () => {
    const md = 'Check [Documentation](https://example.com) with **bold** and _italic_ text.\n\n> Important architectural note.'
    const { container } = render(<MarkdownRenderer content={md} />)

    const link = screen.getByText('Documentation') as HTMLAnchorElement
    expect(link).toBeDefined()
    expect(link.getAttribute('href')).toBe('https://example.com')
    expect(link.className).toBe('markdown-link')

    const bold = screen.getByText('bold')
    expect(bold.tagName).toBe('STRONG')

    const italic = screen.getByText('italic')
    expect(italic.tagName).toBe('EM')

    const quote = container.querySelector('.markdown-blockquote')
    expect(quote).toBeDefined()
    expect(quote?.textContent).toContain('Important architectural note.')
  })

  it('renders multi-level headings (H1, H2, H3, H4)', () => {
    const headingsMd = '# Level 1\n## Level 2\n### Level 3\n#### Level 4'
    render(<MarkdownRenderer content={headingsMd} />)

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Level 1')
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Level 2')
    expect(screen.getByRole('heading', { level: 3 }).textContent).toBe('Level 3')
    expect(screen.getByRole('heading', { level: 4 }).textContent).toBe('Level 4')
  })

  it('suppresses robotic greeting headers and status banner boilerplate', () => {
    const noisyMd = '### Greetings!\n> Status: Online and ready to assist.\n\nHello! How can I help you today?'
    render(<MarkdownRenderer content={noisyMd} />)

    expect(screen.queryByText('Greetings!')).toBeNull()
    expect(screen.queryByText(/Status: Online/)).toBeNull()
    expect(screen.getByText('Hello! How can I help you today?')).toBeDefined()
  })

  it('renders unicode bullet lists (•)', () => {
    const bulletMd = '• First step\n• Second step'
    render(<MarkdownRenderer content={bulletMd} />)

    expect(screen.getByText('First step')).toBeDefined()
    expect(screen.getByText('Second step')).toBeDefined()
  })
})
