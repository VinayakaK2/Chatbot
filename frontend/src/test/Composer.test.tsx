import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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
      'Qwen/Qwen2.5-Coder-14B-Instruct-AWQ',
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
})
