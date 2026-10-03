"""Guarded commands preserve source and operation identity on Community hosts."""
from copy import deepcopy
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MarkablePartnerCase


@tagged('post_install', '-at_install')
class TestMarkupSurfaceCommands(MarkablePartnerCase):
    def setUp(self):
        super().setUp()
        self.host = self.env['res.partner'].create({'name': 'Guarded source', 'comment': '<p>Original passage.</p>'})

    def _mark(self):
        text = self.host.markup_text('comment')
        return self.host.markup_add('comment', 0, len(text), {'motivation': 'editing', 'body': 'New passage.'})

    def _revision(self):
        return self.host._markup_operation_source('comment')['revision']

    def test_accept_retry_changes_source_once(self):
        mark = self._mark()
        revision = self._revision()
        result = self.host.markup_action('comment', 'accept', revision, 'accept-operation-0001', mark_id=mark['id'])
        replay = self.host.markup_action('comment', 'accept', revision, 'accept-operation-0001', mark_id=mark['id'])
        self.assertEqual(result, replay)
        self.assertEqual(self.host.markup_text('comment'), 'New passage.')
        self.assertEqual(self.env['markup.operation'].sudo().search_count([
            ('res_id', '=', self.host.id), ('res_model', '=', self.host._name)]), 1)

    def test_changed_source_refuses_stale_accept(self):
        mark = self._mark()
        revision = self._revision()
        self.host.comment = '<p>Different source.</p>'
        with self.assertRaises(UserError):
            self.host.markup_action('comment', 'accept', revision, 'accept-operation-0002', mark_id=mark['id'])
        self.assertEqual(self.host.markup_text('comment'), 'Different source.')

    def test_same_key_cannot_change_request(self):
        mark = self._mark()
        revision = self._revision()
        self.host.markup_action('comment', 'reset', revision, 'reset-operation-0001', mark_id=mark['id'])
        with self.assertRaises(UserError):
            self.host.markup_action('comment', 'reject', revision, 'reset-operation-0001', mark_id=mark['id'])

    def test_reset_withdraws_without_rejecting_source(self):
        mark = self._mark()
        result = self.host.markup_action('comment', 'reset', self._revision(), 'reset-operation-0002', mark_id=mark['id'])
        self.assertEqual(result['mark']['state'], 'withdrawn')
        self.assertEqual(self.host.markup_text('comment'), 'Original passage.')

    def test_explicit_conversations_fresh_retries_same(self):
        text = self.host.markup_text('comment')
        selection = {'start': 0, 'end': len(text), 'quote': text}
        revision = self._revision()
        one = self.host.markup_action('comment', 'discuss', revision, 'discuss-operation-01', selection=selection)
        replay = self.host.markup_action('comment', 'discuss', revision, 'discuss-operation-01', selection=selection)
        two = self.host.markup_action('comment', 'discuss', revision, 'discuss-operation-02', selection=selection)
        self.assertEqual(one['channel_id'], replay['channel_id'])
        self.assertNotEqual(one['channel_id'], two['channel_id'])
        self.assertEqual(one['agent_state'], 'not_requested')

    def test_no_provider_cannot_promise_agent_response(self):
        self.assertFalse(self.host.markup_surface_capabilities('comment')['ask'])

    def _member(self, suffix):
        return self.env['res.users'].create({
            'name': 'Markup member ' + suffix, 'login': 'markup_member_' + suffix,
            'group_ids': [Command.set([self.env.ref('base.group_user').id, self.env.ref('base.group_partner_manager').id])],
        })

    def _deny(self, operation):
        return self.env['ir.rule'].create({
            'name': 'Deny selected markup host ' + operation,
            'model_id': self.env['ir.model']._get_id('res.partner'),
            'domain_force': "[('id', '!=', %s)]" % self.host.id,
            'perm_read': operation == 'read', 'perm_write': operation == 'write',
            'perm_create': False, 'perm_unlink': False,
        })

    def test_resolver_can_neutrally_withdraw_another_authors_mark(self):
        author = self._member('withdraw_author')
        resolver = self._member('withdraw_resolver')
        text = self.host.markup_text('comment')
        mark = self.host.with_user(author).markup_add('comment', 0, len(text), {'body': 'Author note'})
        host = self.host.with_user(resolver)
        self.assertTrue(host.markup_can_resolve('comment'))
        result = host.markup_action('comment', 'withdraw', host._markup_operation_source('comment')['revision'],
                                    'resolver-withdraw-01', mark_id=mark['id'])
        self.assertEqual(result['mark']['state'], 'withdrawn')
        self.assertEqual(self.host.markup_text('comment'), text)

    def test_read_revocation_denies_receipt_replay(self):
        member = self._member('revoked_read')
        host = self.host.with_user(member)
        text = host.markup_text('comment')
        mark = host.markup_add('comment', 0, len(text), {'body': 'A note'})
        revision = host._markup_operation_source('comment')['revision']
        host.markup_action('comment', 'reset', revision, 'revoked-read-key-01', mark_id=mark['id'])
        self._deny('read')
        with self.assertRaises(AccessError):
            host.markup_action('comment', 'reset', revision, 'revoked-read-key-01', mark_id=mark['id'])

    def test_write_revocation_denies_accept_receipt_replay(self):
        member = self._member('revoked_write')
        host = self.host.with_user(member)
        text = host.markup_text('comment')
        mark = host.markup_add('comment', 0, len(text), {'motivation': 'editing', 'body': 'Accepted source.'})
        revision = host._markup_operation_source('comment')['revision']
        host.markup_action('comment', 'accept', revision, 'revoked-write-key-01', mark_id=mark['id'])
        self._deny('write')
        with self.assertRaises(AccessError):
            host.markup_action('comment', 'accept', revision, 'revoked-write-key-01', mark_id=mark['id'])

    def test_field_revocation_denies_receipt_replay(self):
        member = self._member('revoked_field')
        host = self.host.with_user(member)
        text = host.markup_text('comment')
        mark = host.markup_add('comment', 0, len(text), {'body': 'A note'})
        revision = host._markup_operation_source('comment')['revision']
        host.markup_action('comment', 'reset', revision, 'revoked-field-key-01', mark_id=mark['id'])
        with patch.object(host._fields['comment'], 'groups', 'base.group_system'):
            with self.assertRaises(AccessError):
                host.markup_action('comment', 'reset', revision, 'revoked-field-key-01', mark_id=mark['id'])

    def test_membership_revocation_denies_conversation_replay(self):
        member = self._member('revoked_member')
        host = self.host.with_user(member)
        text = host.markup_text('comment')
        revision = host._markup_operation_source('comment')['revision']
        selection = {'start': 0, 'end': len(text), 'quote': text}
        result = host.markup_action('comment', 'discuss', revision, 'revoked-member-key-1', selection=selection)
        channel = self.env['discuss.channel'].sudo().browse(result['channel_id'])
        channel.channel_member_ids.filtered(lambda row: row.partner_id == member.partner_id).unlink()
        with self.assertRaises(AccessError):
            host.markup_action('comment', 'discuss', revision, 'revoked-member-key-1', selection=selection)

    def test_nonauthor_without_write_cannot_withdraw(self):
        author = self._member('denied_author')
        other = self._member('denied_other')
        text = self.host.markup_text('comment')
        mark = self.host.with_user(author).markup_add('comment', 0, len(text), {'body': 'Owned note'})
        self._deny('write')
        host = self.host.with_user(other)
        self.assertFalse(host.markup_marks('comment')[0]['can_withdraw'])
        with self.assertRaises(AccessError):
            host.markup_action('comment', 'withdraw', host._markup_operation_source('comment')['revision'],
                               'denied-withdraw-01', mark_id=mark['id'])

    def test_portal_discussion_admits_only_the_callers_membership(self):
        portal = self.env['res.users'].create({
            'name': 'Portal discussion caller', 'login': 'markup_portal_caller',
            'group_ids': [Command.set([self.env.ref('base.group_portal').id])],
        })
        self.host = portal.partner_id
        self.host.comment = '<p>My admitted portal passage.</p>'
        host = self.host.with_user(portal)
        text = host.markup_text('comment')
        revision = host._markup_operation_source('comment')['revision']
        result = host.markup_action('comment', 'discuss', revision, 'portal-discuss-key-01',
                                   selection={'start': 0, 'end': len(text), 'quote': text})
        channel = self.env['discuss.channel'].sudo().browse(result['channel_id'])
        self.assertEqual(channel.channel_member_ids.partner_id, portal.partner_id)
        channel.with_user(portal).check_access('read')
        self.assertEqual(result['agent_state'], 'not_requested')

    def test_accept_comment_works_without_a_scoring_adapter(self):
        mark = self._mark()
        result = self.host.markup_action('comment', 'accept_comment', self._revision(),
            'community-comment-01', mark_id=mark['id'], note='This reads better.')
        comment = self.env['markup.mark'].sudo().browse(result['comment_mark']['id'])
        self.assertEqual(comment.motivation, 'replying')
        self.assertEqual(comment.parent_id.id, mark['id'])
        self.assertEqual(comment.body, 'This reads better.')
        self.assertEqual(result['mark']['state'], 'accepted')
        self.assertEqual(self.host.markup_text('comment'), 'New passage.')

    def test_accept_request_change_is_one_pending_linked_reply(self):
        mark = self._mark()
        result = self.host.markup_action('comment', 'accept_request_change', self._revision(),
            'community-request-01', mark_id=mark['id'], note='Please define this term.')
        request = self.env['markup.mark'].sudo().browse(result['request_mark']['id'])
        self.assertEqual(request.motivation, 'replying')
        self.assertEqual(request.state, 'open')
        self.assertEqual(request.request_for_id.id, mark['id'])
        self.assertFalse(request.parent_id)
        self.assertIsNone(result['comment_mark'])
        self.assertEqual(result['mark']['state'], 'accepted')

    def _drawing_fixture(self):
        return {'layers': [], 'viewport': {'tx': 0, 'ty': 0, 'scale': 1}}, {
            'width': 200, 'height': 60, 'scope': 'passage', 'anchor': {
                'target': {'model': self.host._name, 'resId': self.host.id, 'field': 'comment'},
                'start': 0, 'end': 8, 'quote': 'Original', 'prefix': '', 'suffix': ' passage.',
                'geometry': {'width': 200, 'height': 60,
                             'fragments': [{'x': 0, 'y': 0, 'width': 80, 'height': 20}]}}}

    def test_drawing_preserves_exact_selected_passage(self):
        data, bounds = self._drawing_fixture()
        result = self.host.markup_drawing('comment', data, bounds=bounds, session_id='anchor-session')
        self.assertEqual(result['quote'], 'Original')
        self.assertEqual(result['ink']['bounds']['anchor'], bounds['anchor'])

    def test_drawing_refuses_forged_source_target(self):
        data, bounds = self._drawing_fixture()
        bounds['anchor']['target']['resId'] += 1
        with self.assertRaises(UserError):
            self.host.markup_drawing('comment', data, bounds=bounds, session_id='anchor-session')

    def test_drawing_refuses_stale_selected_quote(self):
        data, bounds = self._drawing_fixture()
        self.host.comment = '<p>Replaced passage.</p>'
        with self.assertRaises(UserError):
            self.host.markup_drawing('comment', data, bounds=bounds, session_id='anchor-session')

    def test_drawing_refuses_nonfinite_passage_geometry(self):
        data, bounds = self._drawing_fixture()
        bounds['anchor']['geometry']['fragments'][0]['x'] = float('nan')
        with self.assertRaises(UserError):
            self.host.markup_drawing('comment', data, bounds=bounds, session_id='anchor-session')

    def test_drawing_revision_cannot_mutate_original_anchor(self):
        data, bounds = self._drawing_fixture()
        result = self.host.markup_drawing('comment', data, bounds=bounds, session_id='anchor-session')
        mutated = deepcopy(bounds)
        mutated['anchor']['geometry']['fragments'][0]['width'] = 81
        with self.assertRaises(UserError):
            self.host.markup_drawing('comment', data, mark_id=result['id'], revision=1,
                                     bounds=mutated, session_id='anchor-session')

    def test_unknown_version_marks_remain_individual_without_fake_unit(self):
        first, second = self._mark(), self._mark()
        count = self.env['markup.operation'].sudo().search_count([])
        with self.assertRaises(UserError):
            self.host.markup_merge('comment', [first['id'], second['id']], self._revision(), 'merge-unknown-version')
        self.assertEqual(self.env['markup.operation'].sudo().search_count([]), count)
        self.assertEqual(len(self.host.markup_marks('comment')), 2)
        self.assertFalse(self.host.markup_surface_capabilities('comment')['merge'])
        self.assertFalse(self.host.markup_units('comment')['units'])
