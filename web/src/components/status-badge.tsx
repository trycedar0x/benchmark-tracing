import { AlertTriangle, CheckCircle2, CircleDashed, Loader2, XCircle, type LucideIcon } from 'lucide-react'
import { Badge } from '@/components/ui/badge'

type Tone = { variant: 'default' | 'secondary' | 'destructive' | 'outline'; icon: LucideIcon; spin?: boolean }

const POSITIVE: Tone = { variant: 'secondary', icon: CheckCircle2 }
const NEGATIVE: Tone = { variant: 'destructive', icon: XCircle }
const CAUTION: Tone = { variant: 'outline', icon: AlertTriangle }
const ACTIVE: Tone = { variant: 'outline', icon: Loader2, spin: true }
const NEUTRAL: Tone = { variant: 'outline', icon: CircleDashed }

const TONES: Record<string, Tone> = {
  succeeded: POSITIVE,
  correct: POSITIVE,
  improvement: POSITIVE,
  approved: POSITIVE,
  draft: NEUTRAL,
  failed: NEGATIVE,
  error: NEGATIVE,
  incorrect: NEGATIVE,
  regression: NEGATIVE,
  budget_exceeded: CAUTION,
  cancelled: CAUTION,
  partial: CAUTION,
  running: ACTIVE,
  cancelling: ACTIVE,
  estimating: ACTIVE,
}

export function StatusBadge({ value }: { value: string }) {
  const tone = TONES[value] ?? NEUTRAL
  const Icon = tone.icon
  return (
    <Badge variant={tone.variant}>
      <Icon className={tone.spin ? 'animate-spin' : undefined} />
      {value.replace('_', ' ')}
    </Badge>
  )
}
