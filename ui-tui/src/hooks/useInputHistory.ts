import { useStore } from '@nanostores/react'
import { useMemo, useRef, useState } from 'react'

import { $inputHistoryProject } from '../app/uiStore.js'
import * as inputHistory from '../lib/history.js'

export function useInputHistory() {
  const projectKey = useStore($inputHistoryProject)
  const items = useMemo(() => inputHistory.load(projectKey), [projectKey])
  const historyRef = useRef<string[]>(items)
  const [historyIdx, setHistoryIdx] = useState<number | null>(null)
  const historyDraftRef = useRef('')

  if (historyRef.current !== items) {
    historyRef.current = items
    historyDraftRef.current = ''

    if (historyIdx !== null) {
      setHistoryIdx(null)
    }
  }

  return {
    historyRef, historyIdx, setHistoryIdx, historyDraftRef,
    pushHistory: (line: string) => inputHistory.append(line, projectKey)
  }
}
