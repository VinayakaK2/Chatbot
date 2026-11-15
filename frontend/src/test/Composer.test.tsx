import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { Composer } from '../components/chat/Composer'

describe('Composer Component', () => {
  it('submits prompt text when Send button is clicked', () => {
    const onSendMessage = vi.fn()
    render(<Composer onSendMessage={onSendMessage} />)

    const textarea = screen.getByRole('textbox', { name: /Task prompt input/i })
    fireEvent.change(textarea, { target: { value: 'Fix failing unit tests in core/' } })

    const sendBtn = screen.getByRole('button', { name: /Send prompt/i })
    fireEvent.click(sendBtn)

    expect(onSendMessage).toHaveBeenCalledTimes(1)
    expect(onSendMessage).toHaveBeenCalledWith(
      'Fix failing unit tests in core/',
      [],
      'antigravity/gemini-3.8-flash',
      'high'
    )
  })

  it('submits on Enter key press without Shift key', () => {
    const onSendMessage = vi.fn()
    render(<Composer onSendMessage={onSendMessage} />)

    const textarea = screen.getByRole('textbox', { name: /Task prompt input/i })
    fireEvent.change(textarea, { target: { value: 'Check git status' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })

    expect(onSendMessage).toHaveBeenCalledTimes(1)
  })

  it('does not submit on Shift+Enter key press (allows newline)', () => {
    const onSendMessage = vi.fn()
    render(<Composer onSendMessage={onSendMessage} />)

    const textarea = screen.getByRole('textbox', { name: /Task prompt input/i })
    fireEvent.change(textarea, { target: { value: 'Line 1' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true })

    expect(onSendMessage).not.toHaveBeenCalled()
  })

  it('shows Stop button and triggers onStopGeneration during active execution', () => {
    const onStopGeneration = vi.fn()
    render(
      <Composer
        onSendMessage={() => {}}
        onStopGeneration={onStopGeneration}
        isGenerating={true}
      />
    )

    const stopBtn = screen.getByRole('button', { name: /Stop generation/i })
    expect(stopBtn).toBeDefined()

    fireEvent.click(stopBtn)
    expect(onStopGeneration).toHaveBeenCalledTimes(1)
  })

  it('reads attached text file content and passes it to onSendMessage', async () => {
    const onSendMessage = vi.fn()
    const { container } = render(<Composer onSendMessage={onSendMessage} />)

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    expect(fileInput).toBeDefined()

    const fakeFile = new File(['def add(a, b):\n    return a + b'], 'math.py', {
      type: 'text/x-python',
    })

    fireEvent.change(fileInput, { target: { files: [fakeFile] } })

    // Wait for attachment chip to appear in DOM after async file read
    await waitFor(() => {
      expect(screen.getByText('math.py')).toBeDefined()
    })

    const sendBtn = screen.getByRole('button', { name: /Send prompt/i })
    fireEvent.click(sendBtn)

    expect(onSendMessage).toHaveBeenCalledTimes(1)
    const callArgs = onSendMessage.mock.calls[0]
    expect(callArgs[1].length).toBe(1)
    expect(callArgs[1][0].name).toBe('math.py')
    expect(callArgs[1][0].content).toContain('def add(a, b):')
  })

  it('renders model options with only model names and no extra description text', () => {
    render(<Composer onSendMessage={vi.fn()} />)

    const modelTrigger = screen.getByText('Gemini 3.8 Flash')
    expect(modelTrigger).toBeDefined()

    fireEvent.click(modelTrigger)

    expect(screen.getByText('Qwen 2.5 Coder 14B')).toBeDefined()
    expect(screen.getByText('GPT-4o')).toBeDefined()
    expect(screen.getByText('Claude 3.5 Sonnet')).toBeDefined()

    // Ensure long descriptions are removed as requested
    expect(screen.queryByText(/Fast multimodal vision/i)).toBeNull()
    expect(screen.queryByText(/Deep reasoning multimodal/i)).toBeNull()
    expect(screen.queryByText(/Local \/ Colab vLLM/i)).toBeNull()
  })
})
