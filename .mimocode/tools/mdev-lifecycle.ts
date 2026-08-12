import { tool } from "@mimo-ai/plugin"

export default tool({
  description: "Run the full mdev lifecycle: launch → open → shot → stop (repeatable for soak tests). Wraps the repeated mdev run/open/shot/stop sequence into a single atomic operation with health checks.",
  args: {
    action: tool.schema.enum(["run", "open", "shot", "stop", "full-cycle", "soak"]).describe("Lifecycle action. full-cycle = run→open→shot→stop. soak = repeat full-cycle N times with health checks."),
    name: tool.schema.string().optional().describe("Instance name (default: dev)"),
    url: tool.schema.string().optional().describe("URL to open (for open/full-cycle/soak)"),
    repeats: tool.schema.number().optional().describe("Number of cycles for soak mode (default: 10)"),
    pluginDir: tool.schema.string().optional().describe("Dev plugin directory (-p)"),
    out: tool.schema.string().optional().describe("Screenshot output path"),
    devFlags: tool.schema.string().optional().describe("Dev flags e.g. smbdebug=1"),
  },
  async execute(args, ctx) {
    const name = args.name || "dev"
    const mdev = "python3 support/devtools/mdev"
    const env = "export DISPLAY=:0 WAYLAND_DISPLAY=wayland-0"

    if (args.action === "full-cycle") {
      const url = args.url || "settings:"
      const pluginArg = args.pluginDir ? `-p ${args.pluginDir}` : ""
      const flagsArg = args.devFlags ? `--dev-flags ${args.devFlags}` : ""
      const shotOut = args.out ? `--out ${args.out}` : ""

      const commands = [
        `${env}; ${mdev} run --name ${name} ${pluginArg} ${flagsArg} --force`,
        `${env}; ${mdev} open ${url} --name ${name} --timeout 20`,
        `${env}; ${mdev} shot --name ${name} ${shotOut}`,
        `${env}; ${mdev} stop --name ${name}`,
      ]

      const results: string[] = []
      for (const cmd of commands) {
        const result = await ctx.bash(cmd, { timeout: 30000 })
        results.push(result)
        if (result.exitCode !== 0) {
          return `FAILED at step: ${cmd}\n${result.stdout}\n${result.stderr}`
        }
      }
      return `Full cycle complete for instance '${name}'\n${results.join("\n---\n")}`
    }

    if (args.action === "soak") {
      const repeats = args.repeats || 10
      const url = args.url || "settings:"
      const results: string[] = []
      let passCount = 0
      let failCount = 0

      for (let i = 1; i <= repeats; i++) {
        const pluginArg = args.pluginDir ? `-p ${args.pluginDir}` : ""
        const flagsArg = args.devFlags ? `--dev-flags ${args.devFlags}` : ""

        // Run
        let r = await ctx.bash(`${env}; ${mdev} run --name ${name} ${pluginArg} ${flagsArg} --force`, { timeout: 30000 })
        if (r.exitCode !== 0) { results.push(`[${i}] RUN FAILED: ${r.stderr}`); failCount++; continue }

        // Health check
        r = await ctx.bash(`${env}; ${mdev} health --name ${name}`, { timeout: 10000 })
        if (r.exitCode !== 0) { results.push(`[${i}] HEALTH FAILED: ${r.stderr}`); await ctx.bash(`${env}; ${mdev} stop --name ${name}`, { timeout: 10000 }); failCount++; continue }

        // Open
        r = await ctx.bash(`${env}; ${mdev} open ${url} --name ${name} --timeout 20`, { timeout: 25000 })
        if (r.exitCode !== 0) { results.push(`[${i}] OPEN FAILED: ${r.stderr}`); await ctx.bash(`${env}; ${mdev} stop --name ${name}`, { timeout: 10000 }); failCount++; continue }

        // Shot
        r = await ctx.bash(`${env}; ${mdev} shot --name ${name}`, { timeout: 15000 })
        results.push(`[${i}] PASS shot=${r.exitCode === 0 ? "ok" : "fail"}`)
        if (r.exitCode === 0) passCount++; else failCount++

        // Stop
        await ctx.bash(`${env}; ${mdev} stop --name ${name}`, { timeout: 10000 })
        // Brief settle between cycles
        await new Promise(resolve => setTimeout(resolve, 1000))
      }

      return `Soak complete: ${passCount}/${repeats} PASS, ${failCount}/${repeats} FAIL\n${results.join("\n")}`
    }

    // Single actions
    const pluginArg = args.pluginDir ? `-p ${args.pluginDir}` : ""
    const flagsArg = args.devFlags ? `--dev-flags ${args.devFlags}` : ""
    const url = args.url || ""
    const shotOut = args.out ? `--out ${args.out}` : ""

    const cmdMap: Record<string, string> = {
      run: `${env}; ${mdev} run --name ${name} ${pluginArg} ${flagsArg} --force`,
      open: `${env}; ${mdev} open ${url} --name ${name} --timeout 20`,
      shot: `${env}; ${mdev} shot --name ${name} ${shotOut}`,
      stop: `${env}; ${mdev} stop --name ${name}`,
    }

    const r = await ctx.bash(cmdMap[args.action], { timeout: 30000 })
    return r.stdout + (r.stderr ? `\nSTDERR: ${r.stderr}` : "")
  },
})
