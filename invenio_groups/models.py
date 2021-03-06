# -*- coding: utf-8 -*-
#
# This file is part of Invenio.
# Copyright (C) 2015 CERN.
#
# Invenio is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# Invenio is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Invenio; if not, write to the Free Software Foundation, Inc.,
# 59 Temple Place, Suite 330, Boston, MA 02111-1307, USA.

"""Groups data models."""

from __future__ import absolute_import, print_function, unicode_literals

from datetime import datetime

from flask_login import current_user

from invenio.base.i18n import _
from invenio.ext.login.legacy_user import UserInfo
from invenio.ext.sqlalchemy import db
from invenio.modules.accounts.models import User

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.sql.expression import asc, desc

from sqlalchemy_utils import generic_relationship
from sqlalchemy_utils.types.choice import ChoiceType

from .widgets import RadioGroupWidget
from .signals import group_created, group_deleted


class SubscriptionPolicy(object):

    """Group subscription policies."""

    OPEN = 'O'
    """Users can self-subscribe."""

    APPROVAL = 'A'
    """Users can self-subscribe but requires administrator approval."""

    CLOSED = 'C'
    """Subscription is by administrator invitation only."""

    descriptions = dict([
        (OPEN,
         _('Users can self-subscribe.')),
        (APPROVAL,
         _('Users can self-subscribe but requires administrator approval.')),
        (CLOSED,
         _('Subscription is by administrator invitation only.')),
    ])
    """Policies descriptions."""

    @classmethod
    def describe(cls, policy):
        """Policy description."""
        if cls.validate(policy):
            return cls.descriptions[policy]

    @classmethod
    def validate(cls, policy):
        """Validate subscription policy value."""
        return policy in [cls.OPEN, cls.APPROVAL, cls.CLOSED]


class PrivacyPolicy(object):

    """Group privacy policies."""

    PUBLIC = 'P'
    """Group membership is fully public."""

    MEMBERS = 'M'
    """Group administrators and group members can view members."""

    ADMINS = 'A'
    """Group administrators can view members."""

    descriptions = dict([
        (PUBLIC,
         _('Group membership is fully public.')),
        (MEMBERS,
         _('Only group members can view other members.')),
        (ADMINS,
         _('Only administrators can view members.')),
    ])
    """Policies descriptions."""

    @classmethod
    def describe(cls, policy):
        """Policy description."""
        if cls.validate(policy):
            return cls.descriptions[policy]

    @classmethod
    def validate(cls, policy):
        """Validate privacy policy value."""
        return policy in [cls.PUBLIC, cls.MEMBERS, cls.ADMINS]


class MembershipState(object):

    """Membership state."""

    PENDING_ADMIN = 'A'
    """Pending admin verification."""

    PENDING_USER = 'U'
    """Pending user verification."""

    ACTIVE = 'M'
    """Active membership."""

    @classmethod
    def validate(cls, state):
        """Validate state value."""
        return state in [cls.ACTIVE, cls.PENDING_ADMIN, cls.PENDING_USER]


class Group(db.Model):

    """Group data model."""

    __tablename__ = 'group'

    PRIVACY_POLICIES = [
        (PrivacyPolicy.PUBLIC, _('Public')),
        (PrivacyPolicy.MEMBERS, _('Group members')),
        (PrivacyPolicy.ADMINS, _('Group admins')),
    ]
    """Privacy policy choices."""

    SUBSCRIPTION_POLICIES = [
        (SubscriptionPolicy.OPEN, _('Open')),
        (SubscriptionPolicy.APPROVAL, _('Open with approval')),
        (SubscriptionPolicy.CLOSED, _('Closed')),
    ]
    """Subscription policy choices."""

    id = db.Column(db.Integer(15, unsigned=True), nullable=False,
                   primary_key=True, autoincrement=True)
    """Group identifier."""

    name = db.Column(
        db.String(255), nullable=False, unique=True, index=True,
        info=dict(
            label=_("Name"),
            description=_('Required. A name of a group.'),
        ))
    """Name of group."""

    description = db.Column(
        db.Text, nullable=True, default='',
        info=dict(
            label=_("Description"),
            description=_('Optional. A short description of the group.'
                          ' Default: Empty'),
        ))
    """Description of group."""

    is_managed = db.Column(db.Boolean, default=False, nullable=False)
    """Determine if group is system managed."""

    privacy_policy = db.Column(
        ChoiceType(PRIVACY_POLICIES, impl=db.String(1)), nullable=False,
        default=PrivacyPolicy.ADMINS,
        info=dict(
            label=_('Privacy Policy'),
            widget=RadioGroupWidget(PrivacyPolicy.descriptions),
        )
    )
    """Policy for who can view the list of group members."""

    subscription_policy = db.Column(
        ChoiceType(SUBSCRIPTION_POLICIES, impl=db.String(1)), nullable=False,
        default=SubscriptionPolicy.CLOSED,
        info=dict(
            label=_('Subscription Policy'),
            widget=RadioGroupWidget(SubscriptionPolicy.descriptions),
        )
    )
    """Policy for how users can be subscribed to the group."""

    created = db.Column(db.DateTime, nullable=False, default=datetime.now)
    """Creation timestamp."""

    modified = db.Column(db.DateTime, nullable=False, default=datetime.now,
                         onupdate=datetime.now)
    """Modification timestamp."""

    def get_id(self):
        """Get group id.

        :returns: the group id
        """
        return self.id

    @classmethod
    def create(cls, name=None, description='', privacy_policy=None,
               subscription_policy=None, is_managed=False, admins=None):
        """Create a new group.

        If the group is successfully created, the ``group_created`` signal will
        be sent.

        :param name: Name of group. Required and must be unique.
        :param description: Description of group. Default: ``''``
        :param privacy_policy: PrivacyPolicy
        :param subscription_policy: SubscriptionPolicy
        :param admins: list of user and/or group objects. Default: ``[]``
        :returns: Newly created group
        :raises: IntegrityError: if group with given name already exists
        """
        assert name
        assert privacy_policy is None or PrivacyPolicy.validate(privacy_policy)
        assert subscription_policy is None or \
            SubscriptionPolicy.validate(subscription_policy)
        assert admins is None or isinstance(admins, list)

        try:
            obj = cls(
                name=name,
                description=description,
                privacy_policy=privacy_policy,
                subscription_policy=subscription_policy,
                is_managed=is_managed,
            )
            db.session.add(obj)

            for a in admins or []:
                db.session.add(GroupAdmin(
                    group=obj, admin_id=a.get_id(),
                    admin_type=resolve_admin_type(a)))

            db.session.commit()

            group_created.send(cls, group=obj)

            return obj
        except IntegrityError:
            db.session.rollback()
            raise

    def delete(self):
        """Delete a group and all associated memberships.

        If the group is successfully deleted, the ``group_deleted`` signal will
        be sent.
        """
        try:
            Membership.query_by_group(self).delete()
            GroupAdmin.query_by_group(self).delete()
            GroupAdmin.query_by_admin(self).delete()
            db.session.delete(self)
            db.session.commit()

            group_deleted.send(self.__class__, group=self)
        except Exception:
            db.session.rollback()
            raise

    def update(self, name=None, description=None, privacy_policy=None,
               subscription_policy=None, is_managed=None):
        """Update group.

        :param name: Name of group.
        :param description: Description of group.
        :param privacy_policy: PrivacyPolicy
        :param subscription_policy: SubscriptionPolicy
        :returns: Updated group
        """
        if name is not None:
            self.name = name
        if description is not None:
            self.description = description
        if (
            privacy_policy is not None and
            PrivacyPolicy.validate(privacy_policy)
        ):
            self.privacy_policy = privacy_policy
        if (
            subscription_policy is not None and
            SubscriptionPolicy.validate(subscription_policy)
        ):
            self.subscription_policy = subscription_policy
        if is_managed is not None:
            self.is_managed = is_managed

        db.session.commit()

        return self

    @classmethod
    def get_by_name(cls, name):
        """Query group by a group name.

        :param name: Name of a group to search for.
        :returns: Group object or None.
        """
        try:
            return cls.query.filter_by(name=name).one()
        except NoResultFound:
            return None

    @classmethod
    def query_by_names(cls, names):
        """Query group by a list of group names.

        :param list names: List of the group names.
        :returns: Query object.
        """
        assert isinstance(names, list)
        return cls.query.filter(cls.name.in_(names))

    @classmethod
    def query_by_user(cls, user, with_pending=False, eager=False):
        """Query group by user.

        :param user: User object.
        :param bool with_pending: Whether to include pending users.
        :param bool eager: Eagerly fetch group members.
        :returns: Query object.
        """
        q1 = Group.query.join(Membership).filter_by(id_user=user.get_id())
        if not with_pending:
            q1 = q1.filter_by(state=MembershipState.ACTIVE)
        if eager:
            q1 = q1.options(joinedload(Group.members))

        q2 = Group.query.join(GroupAdmin).filter_by(
            admin_id=user.get_id(), admin_type=resolve_admin_type(user))
        if eager:
            q2 = q2.options(joinedload(Group.members))

        query = q1.union(q2).with_entities(Group.id)

        return Group.query.filter(Group.id.in_(query))

    @classmethod
    def search(cls, query, q):
        """Modify query as so include only specific group names.

        :param query: Query object.
        :param str q: Search string.
        :returs: Query object.
        """
        return query.filter(Group.name.like("%"+q+"%"))

    def add_admin(self, admin):
        """Invite an admin to a group.

        :param admin: Object to be added as an admin.
        :returns: GroupAdmin object.
        """
        return GroupAdmin.create(self, admin)

    def remove_admin(self, admin):
        """Remove an admin from group (independent of membership state).

        :param admin: Admin to be removed from group.
        """
        return GroupAdmin.delete(self, admin)

    def add_member(self, user, state=MembershipState.ACTIVE):
        """Invite a user to a group.

        :param user: User to be added as a group member.
        :param state: MembershipState. Default: MembershipState.ACTIVE.
        :returns: Membership object or None.
        """
        return Membership.create(self, user, state)

    def remove_member(self, user):
        """Remove a user from a group (independent of their membership state).

        :param user: User to be removed from group members.
        """
        return Membership.delete(self, user)

    def invite(self, user, admin=None):
        """Invite a user to a group (should be done by admins).

        Wrapper around ``add_member()`` to ensure proper membership state.

        :param user: User to invite.
        :param admin: Admin doing the action. If provided, user is only invited
            if the object is an admin for this group. Default: None.
        :returns: Newly created Membership or None.
        """
        if admin is None or self.is_admin(admin):
            return self.add_member(user, state=MembershipState.PENDING_USER)
        return None

    def invite_by_emails(self, emails):
        """Invite a users to a group by emails.

        :param list emails: Emails of users that shall be invited.
        :returns: Newly created Membership or None.
        """
        assert emails is None or isinstance(emails, list)

        for email in emails:
            try:
                user = User.query.filter_by(email=email).one()
                return self.invite(user)
            except Exception:
                return None

    def subscribe(self, user):
        """Subscribe a user to a group (done by users).

        Wrapper around ``add_member()`` which checks subscription policy.

        :param user: User to subscribe.
        :returns: Newly created Membership or None.
        """
        if self.subscription_policy == SubscriptionPolicy.OPEN:
            return self.add_member(user)
        elif self.subscription_policy == SubscriptionPolicy.APPROVAL:
            return self.add_member(user, state=MembershipState.PENDING_ADMIN)
        elif self.subscription_policy == SubscriptionPolicy.CLOSED:
            return None

    def is_admin(self, admin):
        """Verify if given admin is the group admin.

        :param admin: Admin to be checked.
        :returns: True or False.
        """
        is_admin = False
        ga = GroupAdmin.get(self, admin)
        if ga is not None:
            is_admin = True
        return is_admin

    def is_member(self, user, with_pending=False):
        """Verify if given user is a group member.

        :param user: User to be checked.
        :param bool with_pending: Whether to include pending users or not.
        :returns: True or False.
        """
        is_member = False
        m = Membership.get(self, user)
        if m is not None:
            if with_pending:
                is_member = True
            elif m.state == MembershipState.ACTIVE:
                is_member = True
        return is_member

    def can_see_members(self, user):
        """Determine if given user can see other group members.

        :param user: User to be checked.
        :returns: True or False.
        """
        if self.privacy_policy == PrivacyPolicy.PUBLIC:
            return True
        elif self.privacy_policy == PrivacyPolicy.MEMBERS:
            return self.is_member(user)
        elif self.privacy_policy == PrivacyPolicy.ADMINS:
            return self.is_admin(user)

    def members_count(self):
        """Determine members count.

        :returns: Number of memberships.
        """
        return Membership.query_by_group(self).count()


class Membership(db.Model):

    """Represent a users membership of a group."""

    MEMBERSHIP_STATE = {
        MembershipState.PENDING_ADMIN: _("Pending admin approval"),
        MembershipState.PENDING_USER: _("Pending member approval"),
        MembershipState.ACTIVE: _("Active"),
    }
    """MembershipState choices."""

    __tablename__ = 'groupMEMBER'

    id_user = db.Column(db.Integer(15, unsigned=True), db.ForeignKey(User.id),
                        nullable=False, primary_key=True)
    """User for membership."""

    id_group = db.Column(
        db.Integer(15, unsigned=True), db.ForeignKey(Group.id), nullable=False,
        primary_key=True)
    """Group for membership."""

    state = db.Column(ChoiceType(MEMBERSHIP_STATE, impl=db.String(1)),
                      nullable=False)
    """State of membership."""

    created = db.Column(db.DateTime, nullable=False, default=datetime.now)
    """Creation timestamp."""

    modified = db.Column(db.DateTime, nullable=False, default=datetime.now,
                         onupdate=datetime.now)
    """Modification timestamp."""

    #
    # Relations
    #

    user = db.relationship(User, backref=db.backref(
        'groups'))
    """User relaionship."""

    group = db.relationship(Group, backref=db.backref(
        'members', cascade="all, delete-orphan"))
    """Group relationship."""

    @classmethod
    def get(cls, group, user):
        """Get Membership for given user and group.

        :param group: Group object.
        :param user: User object.
        :returns: Membership or None.
        """
        try:
            m = cls.query.filter_by(id_user=user.get_id(), group=group).one()
            return m
        except Exception:
            return None

    @classmethod
    def _filter(cls, query, state=MembershipState.ACTIVE, eager=None):
        """Filter a query result."""
        query = query.filter_by(state=state)

        eager = eager or []
        for field in eager:
            query = query.options(joinedload(field))

        return query

    @classmethod
    def query_by_user(cls, user, **kwargs):
        """Get a user's memberships."""
        return cls._filter(
            cls.query.filter_by(id_user=user.get_id()),
            **kwargs
        )

    @classmethod
    def query_invitations(cls, user, eager=False):
        """Get all invitations for given user."""
        if eager:
            eager = [Membership.group]
        return cls.query_by_user(user, state=MembershipState.PENDING_USER,
                                 eager=eager)

    @classmethod
    def query_requests(cls, admin, eager=False):
        """Get all pending group requests."""
        # get direct pending request
        q1 = GroupAdmin.query_by_admin(admin).with_entities(
            GroupAdmin.group_id)
        q2 = Membership.query.filter(
            Membership.state == MembershipState.PENDING_ADMIN,
            Membership.id_group.in_(q1),
        )

        # get request from admin groups your are member of
        q3 = Membership.query_by_user(
            user=admin, state=MembershipState.ACTIVE
        ).with_entities(Membership.id_group)
        q4 = GroupAdmin.query.filter(
            GroupAdmin.admin_type == 'Group', GroupAdmin.admin_id.in_(q3)
        ).with_entities(GroupAdmin.group_id)
        q5 = Membership.query.filter(
            Membership.state == MembershipState.PENDING_ADMIN,
            Membership.id_group.in_(q4))

        query = q2.union(q5)

        return query

    @classmethod
    def query_by_group(cls, group_or_id, with_invitations=False, **kwargs):
        """Get a group's members."""
        if isinstance(group_or_id, Group):
            id_group = group_or_id.id
        else:
            id_group = group_or_id

        if not with_invitations:
            return cls._filter(
                cls.query.filter_by(id_group=id_group),
                **kwargs
            )
        else:
            return cls.query.filter(
                Membership.id_group == id_group,
                db.or_(
                    Membership.state == MembershipState.PENDING_USER,
                    Membership.state == MembershipState.ACTIVE
                )
            )

    @classmethod
    def search(cls, query, q):
        """Modify query as so include only specific members.

        :param query: Query object.
        :param str q: Search string.
        :returs: Query object.
        """
        query = query.join(User).filter(
            db.or_(
                User.nickname.like("%"+q+"%"),
                User.email.like("%"+q+"%")
            )
        )
        return query

    @classmethod
    def order(cls, query, field, s):
        """Modify query as so to order the results.

        :param query: Query object.
        :param str s: Orderinig: ``asc`` or ``desc``.
        :returs: Query object.
        """
        if s == "asc":
            query = query.order_by(asc(field))
        elif s == "desc":
            query = query.order_by(desc(field))
        return query

    @classmethod
    def create(cls, group, user, state=MembershipState.ACTIVE):
        """Create a new membership."""
        try:
            membership = cls(
                id_user=user.get_id(),
                id_group=group.id,
                state=state,
            )
            db.session.add(membership)
            db.session.commit()

            return membership
        except IntegrityError:
            db.session.rollback()
            raise

    @classmethod
    def delete(cls, group, user):
        """Delete membership."""
        try:
            cls.query.filter_by(group=group, id_user=user.get_id()).delete()
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

    def accept(self):
        """Activate membership."""
        self.state = MembershipState.ACTIVE
        db.session.commit()

    def reject(self):
        """Remove membership."""
        try:
            db.session.delete(self)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

    def is_active(self):
        """Check if membership is in an active state."""
        return self.state == MembershipState.ACTIVE


# NOTE: Below database model should be refactored once the ACL system have been
# rewritten to allow efficient list queries (i.e. list me all groups i have
# permissions to)
class GroupAdmin(db.Model):

    """Represent an administrator of a group."""

    __tablename__ = 'groupADMIN'

    __table_args__ = (
        db.UniqueConstraint('group_id', 'admin_type', 'admin_id'),
        db.Model.__table_args__
    )

    id = db.Column(db.Integer(15, unsigned=True), nullable=False,
                   primary_key=True, autoincrement=True)
    """GroupAdmin identifier."""

    group_id = db.Column(
        db.Integer(15, unsigned=True), db.ForeignKey(Group.id), nullable=False,
        primary_key=True)
    """Group for membership."""

    admin_type = db.Column(db.Unicode(255))
    """Generic relationship to an object."""

    admin_id = db.Column(db.Integer)
    """Generic relationship to an object."""

    #
    # Relations
    #

    group = db.relationship(Group, backref=db.backref(
        'admins', cascade="all, delete-orphan"))
    """Group relationship."""

    admin = generic_relationship(admin_type, admin_id)
    """Generic relationship to administrator of group."""

    @classmethod
    def create(cls, group, admin):
        """Create a new group admin.

        :param group: Group object.
        :param admin: Admin object.
        :returns: Newly created GroupAdmin object.
        :raises: IntegrityError
        """
        try:
            obj = cls(
                group=group,
                admin=admin,
            )
            db.session.add(obj)

            db.session.commit()
            return obj
        except IntegrityError:
            db.session.rollback()
            raise

    @classmethod
    def get(cls, group, admin):
        """Get specific GroupAdmin object."""
        try:
            ga = cls.query.filter_by(
                group=group, admin_id=admin.get_id(),
                admin_type=resolve_admin_type(admin)).one()
            return ga
        except Exception:
            return None

    @classmethod
    def delete(cls, group, admin):
        """Delete admin from group.

        :param group: Group object.
        :param admin: Admin object.
        """
        try:
            obj = cls.query.filter(
                cls.admin == admin, cls.group == group).one()
            db.session.delete(obj)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def query_by_group(cls, group):
        """Get all admins for a specific group."""
        return cls.query.filter_by(group=group)

    @classmethod
    def query_by_admin(cls, admin):
        """Get all groups for for a specific admin."""
        return cls.query.filter_by(
            admin_type=resolve_admin_type(admin), admin_id=admin.get_id())

    @classmethod
    def query_admins_by_group_ids(cls, groups_ids=None):
        """Get count of admins per group."""
        assert groups_ids is None or isinstance(groups_ids, list)

        query = db.session.query(
            Group.id, func.count(GroupAdmin.id)
        ).join(
            GroupAdmin
        ).group_by(
            Group.id
        )

        if groups_ids:
            query = query.filter(Group.id.in_(groups_ids))

        return query


#
# Helpers
#


def resolve_admin_type(admin):
    """Determine admin type."""
    if admin is current_user or isinstance(admin, UserInfo):
        return "User"
    else:
        return admin.__class__.__name__
