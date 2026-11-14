/**
 * Production FileDiffViewer Component.
 * Visualizes file modifications with added/removed/context lines and file path headers.
 */

import React, { useState } from 'react'
import type { FileDiff } from '../../types/api'
import { IconChevronDown, IconChevronRight, IconFileCode } from '../common/Icons'

export interface FileDiffViewerProps {
  diff: FileDiff
  initiallyExpanded?: boolean
}

export const FileDiffViewer: React.FC<FileDiffViewerProps> = ({
  diff,
  initiallyExpanded = true,
}) => {
  const [isExpanded, setIsExpanded] = useState(initiallyExpanded)

  return (
    <div className="diff-viewer-container" role="region" aria-label={`Diff for ${diff.file_path}`}>
      <div
        className="diff-viewer-header"
        onClick={() => setIsExpanded((prev) => !prev)}
        role="button"
        tabIndex={0}
        aria-expanded={isExpanded}
      >
        <div className="diff-header-left">
          {isExpanded ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
          <IconFileCode size={16} color="var(--accent-primary)" />
          <span className="diff-file-path">{diff.file_path}</span>
        </div>
        <div className="diff-stats">
          <span className="diff-stat-add">+{diff.added_lines}</span>
          <span className="diff-stat-del">-{diff.removed_lines}</span>
        </div>
      </div>

      {isExpanded && (
        <div className="diff-viewer-body">
          {diff.chunks.map((chunk, chunkIdx) => (
            <div key={chunkIdx} className="diff-chunk">
              <div className="diff-chunk-header">
                @@ -{chunk.old_start},{chunk.old_lines} +{chunk.new_start},{chunk.new_lines} @@
              </div>
              <div className="diff-chunk-lines">
                {chunk.lines.map((line, lineIdx) => {
                  let lineClass = 'diff-line-context'
                  let prefix = ' '
                  let cleanContent = line.content
                  if (line.type === 'add') {
                    lineClass = 'diff-line-add'
                    prefix = '+'
                    if (cleanContent.startsWith('+')) {
                      cleanContent = cleanContent.slice(1)
                    }
                  } else if (line.type === 'delete') {
                    lineClass = 'diff-line-del'
                    prefix = '-'
                    if (cleanContent.startsWith('-')) {
                      cleanContent = cleanContent.slice(1)
                    }
                  }

                  return (
                    <div key={lineIdx} className={`diff-line ${lineClass}`}>
                      <span className="diff-num old-num">{line.old_line_no ?? ''}</span>
                      <span className="diff-num new-num">{line.new_line_no ?? ''}</span>
                      <span className="diff-prefix" aria-hidden="true">
                        {prefix}
                      </span>
                      <span className="diff-content">{cleanContent}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
