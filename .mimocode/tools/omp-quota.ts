import { tool } from "@mimo-ai/plugin"
import { execSync } from "child_process"

// Pattern 5 (evolve): before any heavy subagent dispatch, check channel quotas.
// History: dispatching into exhausted channels wasted cycles and stalled the
// pipeline. The orchestrator should call this tool and pick a channel with
// headroom; the tool returns a recommended channel + fallbacks.
//
// Parses the human-readable `omp usage` output into a per-channel saturation
// summary and a go/no-go recommendation. Wraps the `omp` CLI on PATH via
// execSync (no undocumented ctx helper dependency).

type Channel = {
  name: string
  // worst (max) saturation fraction across that channel's quotas, 0..1
  worst: number
  blocked: boolean // any quota at/over 100%
  detail: string[] // one line per quota line
}

const VENDORS = [
  "anthropic",
  "google antigravity",
  "openai codex",
  "zai",
  "openai",
  "google",
  "mistral",
  "deepseek",
  "xai",
  "nvidia",
]

function pctToFraction(pct: string | undefined, frac: string | undefined): number {
  if (frac !== undefined) {
    const f = parseFloat(frac)
    if (!Number.isNaN(f)) return f
  }
  if (pct !== undefined) {
    const p = parseFloat(pct)
    if (!Number.isNaN(p)) return p / 100
  }
  return -1
}

function parseUsage(raw: string): Channel[] {
  const lines = raw.split(/\r?\n/)
  const channels: Channel[] = []
  let cur: Channel | null = null

  // A quota line looks like one of:
  //   ● Claude 5 Hour   ███░░  25.0% used · resets in 3h14m
  //   ● 7 days  █████░░  92.0% used · resets in 6d1h
  //   ● Usage (Google) (Weekly)  ████  85.7% used · resets in 6d8h
  //   ● ZAI 5 Hours Token Quota  ████  16.0% used · resets in 4h40m
  //   ● Claude Extra Usage  ███░░  $45.58 / $99.00 · 46.0% used
  const quotaRe = /●\s+(.+?)\s+[█▓▒░]+\s+(?:(\d+(?:\.\d+)?)% used)?(?:.*?·\s*(\d+(?:\.\d+)?)% used)?(?:.*?·\s*(\d+(?:\.\d+)?)× quota left)?/

  for (const line of lines) {
    const lower = line.toLowerCase()

    // New vendor block header? (heuristic: line starts with a known vendor,
    // or matches "X — N account[s]")
    const vendorHit = VENDORS.find((v) => lower.startsWith(v))
    if (vendorHit) {
      if (cur) channels.push(cur)
      cur = { name: vendorHit, worst: 0, blocked: false, detail: [] }
      continue
    }

    const m = line.match(quotaRe)
    if (m && cur) {
      const label = m[1].trim()
      const usedPct = m[2] !== undefined ? m[2] : m[3] !== undefined ? m[3] : undefined
      const frac = m[4] !== undefined ? m[4] : undefined
      let sat = pctToFraction(usedPct, undefined)
      // "0.08× quota left" means ~92% used
      if (sat < 0 && frac !== undefined) {
        const left = parseFloat(frac)
        if (!Number.isNaN(left)) sat = Math.max(0, 1 - left)
      }
      if (sat < 0) continue
      cur.worst = Math.max(cur.worst, sat)
      if (sat >= 1) cur.blocked = true
      cur.detail.push(`${label}: ${(sat * 100).toFixed(0)}%${sat >= 1 ? " (BLOCKED)" : ""}`)
    }

    // "capacity: 5h → 0.25/1 account used (0.75× quota left)"
    const capRe = /capacity:.*?\(([\d.]+)× quota left\)/
    const cm = line.match(capRe)
    if (cm && cur) {
      const left = parseFloat(cm[1])
      if (!Number.isNaN(left)) {
        const sat = Math.max(0, 1 - left)
        cur.worst = Math.max(cur.worst, sat)
        if (sat >= 1) cur.blocked = true
      }
    }
  }
  if (cur) channels.push(cur)
  return channels
}

function recommend(channels: Channel[]): {
  usable: Channel[]
  blocked: Channel[]
  pick: Channel | null
} {
  const usable = channels.filter((c) => !c.blocked).sort((a, b) => a.worst - b.worst)
  const blocked = channels.filter((c) => c.blocked)
  // Prefer the least-saturated usable channel.
  const pick = usable.length > 0 ? usable[0] : null
  return { usable, blocked, pick }
}

export default tool({
  description:
    "Pre-flight quota check for heavy subagent dispatch. Wraps `omp usage`, summarizes each channel's saturation, flags blocked channels, and recommends the least-saturated usable channel with fallbacks. Call BEFORE dispatching executors/verifiers to avoid wasting work on exhausted channels (incident-class: quota-stall).",
  args: {
    threshold: tool.schema
      .number()
      .optional()
      .describe("Saturation fraction (0..1) above which a channel is considered risky. Default 0.85 (85%)."),
  },
  async execute(args, ctx) {
    const threshold = args.threshold ?? 0.85

    let out: string
    try {
      out = execSync("omp usage 2>&1", {
        encoding: "utf-8",
        timeout: 20000,
        cwd: ctx.directory,
      })
    } catch (e: any) {
      // execSync throws on non-zero exit AND on timeout; capture both.
      const msg = e?.message ?? String(e)
      const partial = typeof e?.stdout === "string" ? e.stdout : ""
      if (!partial) {
        return `omp-quota: failed to run 'omp usage' — ${msg.slice(0, 200)}`
      }
      out = partial
    }
    if (!out.trim()) {
      return `omp-quota: empty output from 'omp usage'.`
    }

    const channels = parseUsage(out)
    if (channels.length === 0) {
      return `omp-quota: could not parse any channels from 'omp usage'. Raw (first 400 chars):\n${out.slice(0, 400)}`
    }

    const { usable, blocked, pick } = recommend(channels)
    const risky = usable.filter((c) => c.worst >= threshold)

    const lines: string[] = []
    lines.push("## Channel quota summary")
    lines.push("")
    for (const c of channels) {
      const tag = c.blocked ? "BLOCKED" : c.worst >= threshold ? "RISKY" : "ok"
      lines.push(`- **${c.name}** — ${(c.worst * 100).toFixed(0)}% [${tag}]`)
      for (const d of c.detail) lines.push(`    - ${d}`)
    }
    lines.push("")
    lines.push("## Recommendation")
    if (blocked.length > 0) {
      lines.push(
        `Blocked (do not dispatch): ${blocked.map((c) => c.name).join(", ") || "(none)"}`
      )
    }
    if (risky.length > 0) {
      lines.push(
        `Risky (>= ${(threshold * 100).toFixed(0)}%): ${risky.map((c) => c.name).join(", ")}`
      )
    }
    if (pick) {
      const fallbacks = usable
        .filter((c) => c !== pick)
        .slice(0, 3)
        .map((c) => `${c.name} (${(c.worst * 100).toFixed(0)}%)`)
      lines.push(
        `Recommended channel: **${pick.name}** (${(pick.worst * 100).toFixed(0)}% saturated).`
      )
      lines.push(
        `Fallbacks: ${fallbacks.length > 0 ? fallbacks.join(" · ") : "(no other usable channels)"}`
      )
    } else {
      lines.push("NO usable channel — all known channels are BLOCKED. Defer dispatch or wait for reset.")
    }
    return lines.join("\n")
  },
})
