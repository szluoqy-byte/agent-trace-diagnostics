// @vitest-environment jsdom
import {cleanup, fireEvent, render, screen} from '@testing-library/react'
import {afterEach, expect, it} from 'vitest'
import PlaygroundDiff from './PlaygroundDiff'

afterEach(cleanup)

it('keeps both source line numbers through insertions, deletions and collapsed ranges', () => {
  const shared = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh']
  const before = ['start', 'old instruction', 'anchor', ...shared, 'end'].join('\n')
  const after = ['start', 'anchor', 'new instruction', 'another instruction', ...shared, 'end'].join('\n')
  render(<PlaygroundDiff before={before} after={after} originalLabel="原始请求" updatedLabel="当前编辑"/>)

  function numbers(text:string) {
    const row = screen.getByText(text, {selector:'pre'}).closest('.playground-diff-line')
    return Array.from(row?.querySelectorAll('.playground-diff-number') ?? [], number => number.textContent)
  }

  expect(numbers('old instruction')).toEqual(['2', ''])
  expect(numbers('anchor')).toEqual(['3', '2'])
  expect(numbers('new instruction')).toEqual(['', '3'])
  expect(numbers('another instruction')).toEqual(['', '4'])
  fireEvent.click(screen.getByText('展开 8 行相同内容'))
  expect(numbers('first')).toEqual(['4', '5'])
  expect(numbers('end')).toEqual(['11', '12'])
})
