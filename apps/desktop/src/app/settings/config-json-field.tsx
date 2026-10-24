import { useEffect, useState } from 'react'

import { Textarea } from '@/components/ui/textarea'

interface ConfigJsonFieldProps {
  value: unknown
  onChange: (value: unknown) => void
  className?: string
}

export function ConfigJsonField({ value, onChange, className }: ConfigJsonFieldProps) {
  const serialized = JSON.stringify(value ?? [], null, 2)
  const [draft, setDraft] = useState(serialized)
  const [error, setError] = useState(false)

  useEffect(() => {
    setDraft(serialized)
    setError(false)
  }, [serialized])

  return (
    <div>
      <Textarea
        aria-invalid={error}
        aria-label="JSON configuration"
        className={className}
        onChange={event => {
          const text = event.target.value

          setDraft(text)

          let parsed: unknown

          try {
            parsed = JSON.parse(text)
          } catch {
            setError(true)

            return
          }

          setError(false)
          onChange(parsed)
        }}
        value={draft}
      />
      {error ? <p role="alert">Invalid JSON; the last valid setting is unchanged.</p> : null}
    </div>
  )
}
