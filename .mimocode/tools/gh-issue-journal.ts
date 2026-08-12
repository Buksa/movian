import { tool } from "@mimo-ai/plugin"

export default tool({
  description: "Fetch GitHub issue context for the Buksa/movian pipeline: state, labels, comments, and structured DoD extraction. Replaces the repeated gh issue view + jq patterns used in orchestrator sessions.",
  args: {
    issue: tool.schema.number().describe("Issue number"),
    action: tool.schema.enum(["summary", "comments", "labels", "dod", "full"]).describe("What to fetch. summary = state+title+labels. comments = last N comments. labels = current labels. dod = extract DoD from issue body. full = everything."),
    repo: tool.schema.string().optional().describe("Repo (default: Buksa/movian)"),
    lastComments: tool.schema.number().optional().describe("Number of recent comments to fetch (default: 3)"),
  },
  async execute(args, ctx) {
    const repo = args.repo || "Buksa/movian"
    const n = args.lastComments || 3

    if (args.action === "summary") {
      const r = await ctx.bash(
        `gh issue view ${args.issue} -R ${repo} --json state,title,labels,body --jq '{state, title, labels: [.labels[].name], body_preview: .body[0:2000]}'`,
        { timeout: 15000 }
      )
      return r.stdout || `Failed: ${r.stderr}`
    }

    if (args.action === "comments") {
      const r = await ctx.bash(
        `gh issue view ${args.issue} -R ${repo} --json comments --jq '.comments[-${n}:] | .[] | "--- [" + (.createdAt | split("T")[0]) + "] " + .author.login + " ---\n" + .body[0:800] + "\n"'`,
        { timeout: 15000 }
      )
      return r.stdout || `No comments found`
    }

    if (args.action === "labels") {
      const r = await ctx.bash(
        `gh issue view ${args.issue} -R ${repo} --json labels --jq '.labels | [.[] | .name] | join(", ")'`,
        { timeout: 10000 }
      )
      return `Labels: ${r.stdout || "none"}`
    }

    if (args.action === "dod") {
      const r = await ctx.bash(
        `gh issue view ${args.issue} -R ${repo} --json body --jq '.body'`,
        { timeout: 15000 }
      )
      if (!r.stdout) return `No body found`

      const body = r.stdout
      // Extract DoD section
      const dodMatch = body.match(/(?:##\s*(?:DoD|Definition of Done|Критерии приемки|Чек-лист)[\s\S]*?)(?=\n## |\n---|\n$)/i)
      if (dodMatch) {
        return `## DoD for #${args.issue}\n\n${dodMatch[0].trim()}`
      }

      // Fallback: extract numbered checklist items
      const checklist = body.match(/(?:^|\n)\s*[-*]\s*\[[ x]\]\s*.+/g)
      if (checklist) {
        return `## Checklist items for #${args.issue}\n\n${checklist.join("\n").trim()}`
      }

      return `No structured DoD found in #${args.issue} body. Full body:\n\n${body.slice(0, 3000)}`
    }

    if (args.action === "full") {
      const [summary, comments, labels] = await Promise.all([
        ctx.bash(`gh issue view ${args.issue} -R ${repo} --json state,title,labels,body --jq '{state, title, labels: [.labels[].name], body_preview: .body[0:3000]}'`, { timeout: 15000 }),
        ctx.bash(`gh issue view ${args.issue} -R ${repo} --json comments --jq '.comments[-${n}:] | .[] | "--- [" + (.createdAt | split("T")[0]) + "] " + .author.login + " ---\n" + .body[0:800] + "\n"'`, { timeout: 15000 }),
        ctx.bash(`gh issue view ${args.issue} -R ${repo} --json labels --jq '.labels | [.[] | .name] | join(", ")'`, { timeout: 10000 }),
      ])

      return `# Issue #${args.issue} — ${repo}

## State
${summary.stdout || "Failed to fetch"}

## Labels
${labels.stdout || "none"}

## Recent Comments
${comments.stdout || "No comments"}

## DoD
(Use action=dod separately to extract structured DoD)`
    }

    return "Unknown action"
  },
})
