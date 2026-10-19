# Discord reporting — TL Atlas

Each repository uses its own channel/webhook. Reporting is read-only: enabling it
never places exchange orders or turns a research/backtest into sandbox trading.
The existing trading runner is unchanged unless DISCORD_WEBHOOK_URL is set.

## Connect the channel

1. Create an incoming webhook in this bot's Discord text channel (channel settings,
   Integrations, Webhooks). Copy its URL into the host's secret environment variables,
   never into the source code or a GitHub issue.
2. On the service already running this bot, set:

   ```text
   DISCORD_WEBHOOK_URL=<webhook for Atlas's channel>
   DISCORD_BOT_NAME=TL Atlas
   DISCORD_STATE_PATH=/data/discord-atlas-sandbox.sqlite3
   ```

3. Mount a persistent volume at `/data`. The journal, pending messages and sent-message
   IDs live in this SQLite file. Without a volume, redeploying loses notification state.
   Keep separate files for each bot, environment and account configuration. Only one
   deployment/service should publish to a channel from a given state file.
4. Merge the reporting PR and redeploy/restart the existing service. Its always-on runner
   starts a separate reporting worker when the webhook variable is present. No new trading
   API key is required: it reads FP_API_KEY and the existing configured account(s).

For a separate **read-only** process (also works if trading is run through cron/Actions):

```sh
python -m friendsbot.discord
```

Do not start a second publisher with a different SQLite file. The standalone process
needs continuous hosting; a ChatGPT reminder is not involved. The short-lived existing
GitHub Actions trading jobs do not start this worker automatically. Use the always-on
service or host the read-only command separately with a persistent volume.

Preview currently open positions without sending or saving anything:

```sh
python -m friendsbot.discord --preview
```

One reporting poll (use only with the same persistent state):

```sh
python -m friendsbot.discord --once
```

## Messages and schedule

- Confirmed entries: market, side, actual entry, initial SL, target/runner rule,
  initial size, initial risk and strategy/timeframe. Rejected/pending entries are not fills.
- Confirmed exit fills, TP1 where identifiable, working SL changes/BE, complete closures
  with exit reason, net dollars and R. Unknown exit prices/targets remain explicitly unknown.
- Current open positions are introduced on first connection; older closed trades are
  imported into the journal for statistics without spamming historical entry messages.
- Poll interval: 30 seconds plus API/send time. Reports publish on the first successful
  poll after **Monday 00:00 Europe/Brussels** (the complete Monday–Sunday week) and
  **00:00 on the first of the next month** (the complete calendar month). This avoids
  missing the final minute of Sunday/month-end. DST is handled with Europe/Brussels.
- Reports missed during downtime are caught up. First installation begins with the
  current week/month; it does not post every historical week's recap.
- One complete position lifecycle counts as one trade. Partial exits are included in
  its complete result, attributed to its final close date. Open trades are shown separately.
  This is closed-trade performance, not the account balance's cash-flow change in that period.
- Net P&L = realized P&L − absolute fees + signed funding, following the official journal
  definition. R = net P&L / (absolute entry-to-initial-SL distance × original filled size). Wins > $0.005, losses < −$0.005, the rest
  break-even; winrate = wins / (wins + losses), BE excluded. Missing R is not treated as zero.
- Bot order tags and position ids match trades; conservative timestamp fallback ignores
  ambiguous/unmatched/manual positions rather than inventing attribution. Uses all history
  pages instead of a 100-trade cap. Avoid manually scaling a bot's position.
- Message chunks stay below Discord's 2,000-character limit. Mentions are disabled.
  HTTP errors leave messages queued for the next successful poll, without logging webhook
  secrets. An ambiguous timeout after Discord accepted a message (or a crash between sending
  and acknowledging) can duplicate that message; Discord webhooks have no idempotency token.

## Strategy-specific behaviour

Atlas uses the existing bot account selection (FB_ACCOUNT / --account). TP1 is 50% at 2R,
then the existing trading code moves SL to BE and exits via EMA 21 on its **selected timeframe**.
The current implementation selects multiple timeframes, so the reporter does not hardcode
a 5m entry / 15m runner. It does not modify the strategy to match a description.

## Validation

Tests cover fill attribution, pagination, partial exits/original risk, signed funding/fees,
break-even winrate, week/month boundaries, DST, leap-year boundaries, restart deduplication,
failed-send recovery, large report splitting and bot-specific adapters. HTTP is mocked;
a real end-to-end channel test still requires the host's webhook and API configuration.

References:
- https://docs.discord.com/developers/resources/webhook
- https://docs.myfundedperpetuals.com/guides/trading-journal
