import type { NextActionOfferPayload, NextActionRespondResult } from '@hermes/shared/gateway-events'

import type { GatewayRpc } from './interfaces.js'
import { $overlayState, patchOverlayState } from './overlayStore.js'
import { captureDestination, isCurrentDestination } from './submissionDestination.js'

export interface NextActionCard extends NextActionOfferPayload {
  select: (id: string) => void
}

export const secondsRemaining = (deadline: number, now = Date.now()) => Math.max(0, Math.ceil((deadline - now) / 1000))

export function closeNextAction(id?: string) {
  const card = $overlayState.get().nextAction
  if (card && (id === undefined || card.id === id)) {
    patchOverlayState({ nextAction: null })
  }
}

export function showNextAction(sid: string, offer: NextActionOfferPayload, rpc: GatewayRpc, sys: (text: string) => void) {
  if (!secondsRemaining(offer.deadline_ms)) {
    return
  }

  const destination = captureDestination()
  const card: NextActionCard = {
    ...offer,
    select(id) {
      if (!isCurrentDestination(destination) || $overlayState.get().nextAction !== card ||
          !offer.actions.some(action => action.id === id)) {
        return
      }

      closeNextAction(offer.id)
      if (!secondsRemaining(offer.deadline_ms)) {
        return
      }

      void rpc<NextActionRespondResult>('interaction.next_action.respond', {
        action_id: id,
        offer_id: offer.id,
        session_id: sid
      }).then(result => {
        if (result?.output && isCurrentDestination(destination)) {
          sys(result.output)
        }
      }).catch(() => {
        if (isCurrentDestination(destination)) {
          sys('The next action could not be selected.')
        }
      })
    }
  }

  patchOverlayState({ nextAction: card })
}
