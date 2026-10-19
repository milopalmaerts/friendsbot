"""Discord channel for TL Atlas. Never places or changes an exchange order."""
import argparse
import os

from .discord_reporting import Reporter, collect_account, loop


def collect(client, account_id=None):
    from .live import find_account, parse_client_id
    account = find_account(client, account_id)
    if not account:
        raise ValueError('Configured Friendsbot account not found')

    def targets(entry, stop, side, order, meta):
        tp = entry + (1 if side == 'long' else -1) * 2 * abs(entry-stop)
        return {'partial_tp': True,
                'target_text': f'TP1: {tp:,.2f} (50% at 2R); then SL to BE. '
                               f'Runner: candle close through EMA 21 on {meta["tf"]}.'}
    return collect_account(client, account['id'], parse_client_id, targets)


def configured(client, account_id=None):
    url = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
    if not url or not client.authenticated:
        return None
    return Reporter(os.environ.get('DISCORD_STATE_PATH', f'state/discord-atlas-{client.environment}.sqlite3'),
                    os.environ.get('DISCORD_BOT_NAME', 'TL Atlas'), url)


def start(client, account_id=None, dry_run=False):
    """A separate read-only worker; notifications never delay protective orders."""
    import threading
    reporter = configured(client, account_id)
    if reporter is not None and not dry_run:
        threading.Thread(target=loop, args=(lambda: collect(client, account_id), reporter), daemon=True).start()


def main():
    from .mfp import Client
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--preview', action='store_true', help='Print current positions, without sending or saving')
    args = parser.parse_args()
    client = Client()
    if not client.authenticated:
        parser.error('FP_API_KEY must be set')
    if args.preview:
        from .discord_reporting import trade_text
        for row in collect(client):
            if not row['closed']:
                print(trade_text(row, 'OPEN POSITION'))
        return
    reporter = configured(client)
    if reporter is None:
        parser.error('DISCORD_WEBHOOK_URL must be set')
    if args.once:
        reporter.poll(collect(client))
    else:
        loop(lambda: collect(client), reporter)


if __name__ == '__main__':
    main()
