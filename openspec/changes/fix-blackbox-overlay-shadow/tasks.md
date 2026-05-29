# Tasks: Fix Blackbox-Only Overlay Shadow Issue

## Task 1: Copy Deliverables to Overlay in blackboxOnly Mode
**File:** `apps/cli/src/commands/start.ts`

- [x] After the overlay directory creation loop (line ~77), add a conditional block gated on `args.blackboxOnly`
- [x] Source: `path.join(repo.hostPath, '.shannon', 'deliverables')`
- [x] Destination: `path.join(workspacePath, 'deliverables')`
- [x] Use `fs.cpSync(src, dst, { recursive: true })` for each entry, skipping `.git`
- [x] Guard with `fs.existsSync(srcDir)` — if missing, let the workflow's own validation catch it

## Task 2: Verify the Fix
**Manual verification:**

- [x] Run `./shannon start -u https://testheader.futunn.com -r /root/code/official_common_header_footer --blackbox-only --debug`
- [x] Confirm `validateDeliverablesExist` passes (no MissingDeliverablesError)
- [ ] Confirm exploit agents run and produce outputs
- [x] Run `pnpm biome` and `pnpm run check` — zero new issues
