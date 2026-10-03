"""Host-authorized commands with durable, idempotent operation receipts."""
import hashlib
import json
import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import SQL

from ..tools import anchor


class MarkupOperation(models.Model):
    _name = 'markup.operation'
    _description = 'Markup Operation Receipt'
    _order = 'id desc'

    res_model = fields.Char(required=True, index=True, help='Model owning the admitted operation.')
    res_id = fields.Many2oneReference(model_field='res_model', required=True, index=True,
                                     help='Host record owning the operation.')
    field_name = fields.Char(required=True, help='Admitted source field.')
    operation_id = fields.Char(required=True, index=True, help='Client operation key for network retries.')
    author_id = fields.Many2one('res.partner', required=True, ondelete='restrict',
                               help='Principal who explicitly requested the operation.')
    action = fields.Char(required=True, help='Requested lifecycle command.')
    request_hash = fields.Char(required=True, help='Hash of the complete request for conflict detection.')
    expected_revision = fields.Char(required=True, help='Source revision the caller explicitly selected.')
    before_json = fields.Json(help='Immutable admitted source and mark evidence before this operation.')
    after_json = fields.Json(help='Immutable source and mark evidence after this operation.')
    result_json = fields.Json(help='Durable result returned to every retry of this operation.')
    channel_id = fields.Many2one('discuss.channel', ondelete='restrict',
                                help='Fresh native conversation created by this explicit invocation.')

    _unique_operation = models.Constraint(
        'UNIQUE(res_model, res_id, author_id, operation_id)',
        'An explicit markup operation has one durable receipt.',
    )

    def write(self, vals):
        if any(operation.result_json for operation in self):
            raise UserError('Completed markup receipts are immutable.')
        return super().write(vals)

    def unlink(self):
        raise UserError('Markup operation evidence cannot be deleted.')


class MarkupSurface(models.AbstractModel):
    _inherit = 'markup.mixin'

    def _markup_surface_revision(self, field_name):
        self.ensure_one()
        value = str(self[field_name] or '')
        return hashlib.sha256(value.encode()).hexdigest()

    def _markup_operation_source(self, field_name):
        """Use the host's source authority when supplied, otherwise native field text."""
        descriptor = getattr(self, '_markup_source_descriptor', None)
        if descriptor:
            return descriptor(field_name)
        self._markup_check_field(field_name)
        self._markup_check_host_read()
        if not self._has_field_access(self._fields[field_name], 'read'):
            raise AccessError('This source field is not readable.')
        return {'revision': self._markup_surface_revision(field_name),
                'value': str(self[field_name] or ''),
                'kind': self._fields[field_name].type,
                'can_write': self.markup_can_resolve(field_name)}

    def _markup_operation_snapshot(self, field_name):
        """Hosts extend this with exact native immutable version references."""
        return dict(self._markup_operation_source(field_name))

    def _markup_operation_feedback(self, mark, note):
        # Native mark lifecycle and comments do not require a scoring adapter.
        return False

    def _markup_operation_comment(self, mark, note, pending=False):
        text = self.markup_text(mark.field_name)
        start = min(mark.start_pos, len(text))
        length = len(mark.body or '') if mark.state == 'accepted' and mark.motivation == 'editing' else mark.end_pos - mark.start_pos
        end = min(len(text), start + length)
        created = self.markup_add(mark.field_name, start, end, {'motivation': 'replying', 'body': note})
        comment = self.env['markup.mark'].sudo().browse(created['id'])
        comment.write({'request_for_id': mark.id, 'parent_id': False if pending else mark.id})
        return comment

    def _markup_operation_negative_feedback(self, note):
        raise UserError('Native negative feedback is unavailable on this installation.')

    def _markup_operation_begin(self, field_name, expected_revision, operation_id, action, request):
        """Reserve a serialized command receipt, or return an admitted retry."""
        self.ensure_one()
        self._markup_check_field(field_name)
        self._markup_can_comment(field_name)
        source = self._markup_operation_source(field_name)
        if action in ('edit_content', 'insert_canvas', 'fork_content') and not source.get('can_write'):
            raise AccessError('This source is no longer writable.')
        if action in ('accept', 'accept_comment', 'accept_request_change', 'rollback', 'rollback_retry', 'rollback_retry_feedback'):
            self._markup_can_resolve(field_name)
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', operation_id):
            raise UserError('An operation needs a stable retry key of 16 to 100 characters.')
        if not isinstance(expected_revision, str) or not expected_revision:
            raise UserError('Select the current source revision before acting.')
        try:
            digest = hashlib.sha256(json.dumps(dict(action=action, field=field_name,
                revision=expected_revision, request=request), sort_keys=True, allow_nan=False).encode()).hexdigest()
        except (TypeError, ValueError):
            raise UserError('The operation contains invalid values.') from None
        self.env.cr.execute(SQL('SELECT id FROM %s WHERE id = %s FOR UPDATE',
                                SQL.identifier(self._table), self.id))
        self.invalidate_recordset()
        Receipt = self.env['markup.operation'].sudo()
        receipt = Receipt.search([('res_model', '=', self._name), ('res_id', '=', self.id),
            ('author_id', '=', self.env.user.partner_id.id), ('operation_id', '=', operation_id)], limit=1)
        if receipt:
            if receipt.request_hash != digest or receipt.field_name != field_name:
                raise UserError('This operation key already belongs to another request.')
            if receipt.channel_id:
                channel = receipt.channel_id.with_user(self.env.user)
                channel.check_access('read')
                if not channel.is_member:
                    raise AccessError('This conversation is no longer admitted.')
            return receipt, receipt.result_json
        source = self._markup_operation_source(field_name)
        if source['revision'] != expected_revision:
            raise UserError('This source changed elsewhere. Reload before acting.')
        receipt = Receipt.create({'res_model': self._name, 'res_id': self.id,
            'field_name': field_name, 'operation_id': operation_id,
            'author_id': self.env.user.partner_id.id, 'action': action,
            'request_hash': digest, 'expected_revision': expected_revision,
            'before_json': self._markup_operation_snapshot(field_name)})
        return receipt, None

    def _markup_operation_finish(self, receipt, field_name, result):
        """Seal the receipt after the native mutation; all changes share one transaction."""
        self.ensure_one()
        if (receipt.res_model, receipt.res_id, receipt.field_name) != (self._name, self.id, field_name):
            raise UserError('The receipt belongs to another source.')
        after = self._markup_operation_snapshot(field_name)
        result = dict(result, operation_id=receipt.operation_id, revision=after['revision'])
        receipt.write({'after_json': after, 'result_json': result})
        return result

    def _markup_operation_reassociated(self, mark):
        return None

    def _markup_launch_invocation(self, channel, mark, receipt):
        raise UserError('No agent response provider is configured for this surface.')

    def _markup_invocation_available(self):
        return False

    def _markup_operation_restore(self, field_name, target_version):
        raise UserError('Version restore is unavailable on this host.')

    def _markup_restore_available(self, field_name):
        return False

    def _markup_fork_available(self, field_name):
        return False

    def markup_editor_fork(self, field_name, value, expected_revision, operation_id, origin_selector, locator=None):
        raise UserError('This source does not expose native version branching.')

    def markup_surface_capabilities(self, field_name):
        self.ensure_one()
        source = self._markup_operation_source(field_name)
        return {'accept': bool(source.get('can_write')),
                'fork': bool(source.get('can_write')) and self._markup_fork_available(field_name),
                'merge': self.markup_can_comment(field_name) and self._markup_unit_supported(field_name),
                'reject': self.markup_can_comment(field_name),
                'reset': self.markup_can_comment(field_name),
                'withdraw': self.markup_can_comment(field_name),
                'update': self.markup_can_comment(field_name),
                'ask': self.markup_can_comment(field_name) and self._markup_invocation_available(),
                'rollback': self._markup_restore_available(field_name),
                'retry': self._markup_invocation_available(),
                'agent_response': self._markup_invocation_available(),
                'discuss': self.markup_can_comment(field_name),
                'revision': source['revision']}

    def _markup_unit_supported(self, field_name):
        return False

    def _markup_unit_version(self, field_name, mark):
        """A host must return a validated immutable native version reference."""
        return None

    def _markup_unit_members(self, field_name, mark_ids, exact=True):
        self.ensure_one()
        self._markup_check_field(field_name)
        self.markup_text(field_name)
        if (not isinstance(mark_ids, list) or not 2 <= len(mark_ids) <= 50
                or any(type(value) is not int or value <= 0 for value in mark_ids)
                or len(set(mark_ids)) != len(mark_ids)):
            raise UserError('Select two to fifty distinct saved markups.')
        marks = self.env['markup.mark'].sudo().browse(sorted(mark_ids)).exists()
        if len(marks) != len(mark_ids) or any(
                (mark.res_model, mark.res_id, mark.field_name) != (self._name, self.id, field_name)
                or mark.parent_id for mark in marks):
            raise AccessError('All members must be admitted root markups on this exact source.')
        if not exact:
            return marks, None
        versions = [self._markup_unit_version(field_name, mark) for mark in marks]
        if not versions[0] or any(version != versions[0] for version in versions):
            raise UserError('Merge requires exact references to the same saved source version.')
        return marks, versions[0]

    def markup_merge(self, field_name, mark_ids, expected_revision, operation_id, title=''):
        """Group existing markups, never apply, resolve or withdraw their originals."""
        if not isinstance(title, str) or len(title) > 200:
            raise UserError('A work unit title is at most two hundred characters.')
        marks, version = self._markup_unit_members(field_name, mark_ids)
        request = {'mark_ids': sorted(mark_ids), 'title': title}
        receipt, replay = self._markup_operation_begin(field_name, expected_revision, operation_id,
                                                      'merge_markups', request)
        # Recheck under the host lock, including retries after access revocation.
        marks, current_version = self._markup_unit_members(field_name, mark_ids)
        if current_version != version:
            raise UserError('A selected markup changed its source version.')
        if replay is not None:
            return replay
        self.env.cr.execute(SQL('SELECT id FROM markup_mark WHERE id IN %s ORDER BY id FOR UPDATE',
                                tuple(marks.ids)))
        marks.invalidate_recordset()
        marks, version = self._markup_unit_members(field_name, mark_ids)
        snapshots = marks.to_dict()
        for snapshot in snapshots:
            snapshot.update(snapshot=True, can_withdraw=False, can_edit_ink=False)
        unit = {'operation_id': operation_id, 'title': title or 'Grouped markups',
                'created': fields.Datetime.to_string(receipt.create_date),
                'author': self.env.user.partner_id.display_name,
                'source': {'model': self._name, 'res_id': self.id, 'field': field_name},
                'version': version, 'mark_ids': marks.ids, 'marks': snapshots, 'can_withdraw': False}
        return self._markup_operation_finish(receipt, field_name,
            {'ok': True, 'unit': unit, 'message': 'Markups grouped; originals are unchanged.',
             'refreshMarks': False})

    def markup_units(self, field_name, before=None, limit=50):
        self.ensure_one()
        self._markup_check_field(field_name)
        self.markup_text(field_name)
        if type(limit) is not int or not 1 <= limit <= 50 or (before is not None and type(before) is not int):
            raise UserError('Select a bounded work unit page.')
        # Other actors' operation receipts remain private. Group members themselves
        # are host-admitted, but that does not widen receipt visibility.
        domain = [('res_model', '=', self._name), ('res_id', '=', self.id),
                  ('field_name', '=', field_name), ('action', '=', 'merge_markups'),
                  ('author_id', '=', self.env.user.partner_id.id)]
        if before is not None:
            domain.append(('id', '<', before))
        receipts = self.env['markup.operation'].sudo().search(domain, order='id desc', limit=limit + 1)
        units = []
        for receipt in receipts[:limit]:
            unit = (receipt.result_json or {}).get('unit')
            if not unit:
                continue
            try:
                _members, _version = self._markup_unit_members(field_name, unit['mark_ids'], exact=False)
            except (AccessError, UserError):
                continue
            units.append(unit)
        return {'units': units, 'more': len(receipts) > limit,
                'next_before': receipts[limit - 1].id if len(receipts) > limit else None}

    def markup_action(self, field_name, action, expected_revision, operation_id,
                      mark_id=None, note=None, target_version=None, selection=None):
        """Execute one explicit command; repeats return its admitted receipt."""
        self.ensure_one()
        self._markup_check_field(field_name)
        if action not in ('accept', 'accept_comment', 'accept_request_change', 'reject', 'reset', 'withdraw', 'update', 'ask', 'discuss', 'rollback', 'retry', 'rollback_retry', 'rollback_retry_feedback'):
            raise UserError('This lifecycle action is not implemented on this host.')
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', operation_id):
            raise UserError('An operation needs a stable retry key of 16 to 100 characters.')
        if not isinstance(expected_revision, str) or not expected_revision:
            raise UserError('Select the current source revision before acting.')
        if note is not None and (not isinstance(note, str) or len(note) > 20000):
            raise UserError('A feedback note must be text of at most 20,000 characters.')
        verb = {'accept_comment': 'accept', 'accept_request_change': 'accept',
                'rollback_retry_feedback': 'rollback_retry'}.get(action, action)
        if action in ('accept_comment', 'accept_request_change', 'rollback_retry_feedback') and not note:
            raise UserError('This action requires the comment or change request text.')
        if verb in ('rollback', 'rollback_retry'):
            self._markup_can_resolve(field_name)
        if verb in ('ask', 'retry', 'rollback_retry') and not self._markup_invocation_available():
            raise UserError('No agent response provider is configured for this surface.')
        self._markup_can_comment(field_name)
        if verb == 'accept':
            self._markup_can_resolve(field_name)
        if action in ('reject', 'withdraw') and mark_id:
            replay_mark = self.env['markup.mark'].sudo().browse(mark_id).exists()
            if replay_mark and replay_mark.author_id != self.env.user.partner_id:
                self._markup_can_resolve(field_name)
        request = dict(mark=mark_id, note=note, selection=selection, target_version=target_version)
        receipt, replay = self._markup_operation_begin(
            field_name, expected_revision, operation_id, action, request)
        if replay is not None:
            return replay
        mark = self.env['markup.mark'].sudo().browse(mark_id).exists() if mark_id else None
        if mark and (mark.res_model, mark.res_id, mark.field_name) != (self._name, self.id, field_name):
            raise UserError('That mark does not belong to the selected region.')
        if verb not in ('ask', 'discuss', 'rollback', 'rollback_retry', 'retry'):
            if not mark or (mark.res_model, mark.res_id, mark.field_name) != (self._name, self.id, field_name):
                raise UserError('That mark does not belong to the selected region.')
            self.env.cr.execute('SELECT id FROM markup_mark WHERE id = %s FOR UPDATE', [mark.id])
            mark.invalidate_recordset()
            if mark.state not in ('open', 'orphaned'):
                raise UserError('This mark is already settled.')
            if action == 'withdraw' and mark.author_id != self.env.user.partner_id:
                self._markup_can_resolve(field_name)
            if action in ('reset', 'update') and mark.author_id != self.env.user.partner_id:
                raise AccessError('Only the author may withdraw or update this mark.')
            if action == 'reject' and mark.author_id != self.env.user.partner_id:
                self._markup_can_resolve(field_name)
        before = self._markup_operation_snapshot(field_name)
        before['mark'] = mark.to_dict()[0] if mark else None
        receipt.write({'before_json': before})
        channel = None
        comment_mark = None
        feedback = False
        if verb in ('accept', 'reject'):
            if mark.state != 'open':
                raise UserError('An orphaned mark must be updated before resolving it.')
            # Check the quoted selection against today's admitted text before splicing.
            text = self.markup_text(field_name)
            if text[mark.start_pos:mark.end_pos] != (mark.quote or ''):
                raise UserError('The quoted source changed. Update the mark before resolving it.')
            self.with_context(markup_feedback_note=note).markup_resolve(mark.id, 'accepted' if verb == 'accept' else 'rejected')
            if note and action != 'accept_request_change':
                comment_mark = self._markup_operation_comment(mark, note)
            feedback = self._markup_operation_feedback(mark, note)
        elif action in ('reset', 'withdraw'):
            mark.write({'state': 'withdrawn', 'resolved_by_id': self.env.user.partner_id.id,
                        'resolved_on': fields.Datetime.now()})
        elif action == 'update':
            text = self.markup_text(field_name)
            resolved = anchor.resolve(text, mark.quote or '', mark.prefix or '', mark.suffix or '', mark.start_pos)
            if not resolved:
                raise UserError('The quoted selection is unavailable in this revision.')
            mark.write({**anchor.selector(text, resolved[0], resolved[1]), 'state': 'open',
                        'version_ref': self._markup_version_ref(field_name)})
            self._markup_operation_reassociated(mark)
        elif verb == 'rollback':
            self._markup_operation_restore(field_name, target_version)
        else:
            if verb == 'rollback_retry':
                self._markup_operation_restore(field_name, target_version)
            if action == 'rollback_retry_feedback':
                feedback = self._markup_operation_negative_feedback(note)
            if selection is None:
                region = self.markup_text(field_name)
                selection = {'start': 0, 'end': len(region), 'quote': region}
            if not isinstance(selection, dict) or type(selection.get('start')) is not int or type(selection.get('end')) is not int:
                raise UserError('Select an exact passage before opening a conversation.')
            text = self.markup_text(field_name)
            start, end = selection['start'], selection['end']
            if not 0 <= start <= end <= len(text) or selection.get('quote') != text[start:end]:
                raise UserError('The selected passage changed. Select it again.')
            created = self.markup_add(field_name, start, end, {'motivation': 'commenting', 'body': note or ''})
            mark = self.env['markup.mark'].sudo().browse(created['id'])
            channel = self.env['discuss.channel'].sudo()._create_group(
                [self.env.user.partner_id.id], name='Selected passage discussion').with_user(self.env.user)
            from markupsafe import Markup
            channel.message_post(body=Markup('<p>%s</p><blockquote>%s</blockquote>') %
                                 ('Discussion opened for this selected passage.', text[start:end]),
                                 message_type='comment', subtype_xmlid='mail.mt_comment')
        request_mark = None
        if action == 'accept_request_change':
            request_mark = self._markup_operation_comment(mark, note, pending=True)
        invocation = self._markup_launch_invocation(channel, mark, receipt) if channel and action != 'discuss' else None
        after = self._markup_operation_snapshot(field_name)
        after['mark'] = mark.to_dict()[0] if mark else None
        messages = {'accept': 'The selected mark was accepted.',
                    'accept_comment': 'The selected mark was accepted and its comment recorded.',
                    'accept_request_change': 'The selected mark was accepted; its change request remains pending.',
                    'reject': 'The selected mark was rejected.',
                    'withdraw': 'The selected mark was withdrawn; its evidence is preserved.',
                    'reset': 'Your selected mark was withdrawn; its evidence is preserved.',
                    'update': 'Your selected mark was explicitly anchored to this revision.',
                    'rollback': 'The selected saved version was restored as a new head.',
                    'discuss': 'A fresh conversation was opened for this selected region.'}
        result = {'ok': True, 'refreshMarks': True,
                  'message': messages.get(action, 'A fresh conversation was opened and one agent response was queued.'),
                  'request_mark': request_mark.to_dict()[0] if request_mark else None,
                  'comment_mark': comment_mark.to_dict()[0] if comment_mark else None,
                  'operation_id': operation_id, 'action': action, 'revision': after['revision'],
                  'mark': after['mark'], 'channel_id': channel.id if channel else False,
                  'feedback': feedback, 'note': note or '', 'agent_state': invocation.get('state') if invocation else 'not_requested',
                  'invocation': invocation}
        receipt.write({'after_json': after, 'channel_id': channel.id if channel else False,
                       'result_json': result})
        return result


class MarkupMarkSurface(models.Model):
    _inherit = 'markup.mark'

    request_for_id = fields.Many2one('markup.mark', ondelete='restrict', readonly=True,
                                    help='Accepted mark whose result this pending change request addresses.')


    def to_dict(self):
        rows = super().to_dict()
        for mark, row in zip(self, rows):
            host = self.env[mark.res_model].sudo(False).browse(mark.res_id)
            row['can_withdraw'] = mark.state in ('open', 'orphaned') and (mark.author_id == self.env.user.partner_id or host.markup_can_resolve(mark.field_name))
            row['request_for_id'] = mark.request_for_id.id or None
            row['created'] = fields.Datetime.to_string(mark.create_date)
        return rows
