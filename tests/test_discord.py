import os
from unittest import TestCase, mock
from friendsbot import discord


class AdapterTests(TestCase):
    def test_channel_disabled_without_webhook_and_during_dry_run(self):
        client=mock.Mock(authenticated=True,environment='test')
        with mock.patch.dict(os.environ,{'DISCORD_WEBHOOK_URL':''}):
            self.assertIsNone(discord.configured(client))
        with mock.patch.object(discord,'configured',return_value=mock.Mock()), mock.patch('threading.Thread') as thread:
            discord.start(client,dry_run=True)
            thread.assert_not_called()

    def test_targets_use_the_actual_selected_timeframe(self):
        client=mock.Mock(authenticated=True,environment='test')
        with mock.patch('friendsbot.live.find_account',return_value={'id':'account'}), \
             mock.patch.object(discord,'collect_account',return_value=[]) as collector:
            discord.collect(client)
            target=collector.call_args.args[3](100,102,'short',{}, {'tf':'30m'})
        self.assertIn('96.00',target['target_text'])
        self.assertIn('30m',target['target_text'])
        self.assertTrue(target['partial_tp'])
