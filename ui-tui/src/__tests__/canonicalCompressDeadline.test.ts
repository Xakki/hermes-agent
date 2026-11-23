import { JsonRpcRequestChannel } from '@hermes/shared/json-rpc-channel'
import { expect, it, vi } from 'vitest'

import { mutateCanonical } from '../app/slash/canonicalSessionControls.js'

it('accepts a slow compression receipt without extending other mutation deadlines', async () => {
  vi.useFakeTimers()
  const channel = new JsonRpcRequestChannel()
  const receipt = { session_id: 's', operation: 'compress', revision: 2, execution_generation: 2 }

  channel.attach({
    send: text => {
      const { id, method, params } = JSON.parse(text)
      const reply = (result: Record<string, unknown>) => channel.handleFrame(JSON.stringify({ id, jsonrpc: '2.0', result }))

      if (method === 'session.resume') {
        reply({ revision: 1, execution_generation: 1, session_id: params.session_id })
      } else if (params.operation === 'compress') {
        setTimeout(() => reply(receipt), 180_000)
      }
    }
  })

  try {
    const gateway = { request: channel.request.bind(channel) }
    const rename = mutateCanonical(gateway, 'other', 'rename', { title: 'New' }).catch((error: Error) => error.message)
    const compress = mutateCanonical(gateway, 's', 'compress', {}).catch((error: Error) => error.message)

    await vi.advanceTimersByTimeAsync(120_000)
    expect(await rename).toBe('request timed out after 120s: session.mutate')
    await vi.advanceTimersByTimeAsync(60_000)
    expect(await compress).toMatchObject({ result: receipt })
  } finally {
    channel.detach(new Error('test ended'))
    vi.useRealTimers()
  }
})
