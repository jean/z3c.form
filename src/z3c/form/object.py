##############################################################################
#
# Copyright (c) 2007 Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""ObjectWidget related classes

$Id$
"""
__docformat__ = "reStructuredText"
import zope.i18n.format
import zope.interface
import zope.component
import zope.schema
import zope.event
import zope.lifecycleevent
from zope.security.proxy import removeSecurityProxy
from z3c.form.converter import BaseDataConverter

from z3c.form import form, interfaces, util, widget
from z3c.form.field import Fields
from z3c.form.error import MultipleErrors
from z3c.form.i18n import MessageFactory as _

def getIfName(iface):
    return iface.__module__+'.'+iface.__name__

class ObjectSubForm(form.BaseForm):
    zope.interface.implements(interfaces.ISubForm)

    def __init__(self, context, request, parentWidget):
        self.context = context
        self.request = request
        self.__parent__ = parentWidget
        self.parentForm = parentWidget.form

    def _validate(self):
        for widget in self.widgets.values():
            try:
                # convert widget value to field value
                converter = interfaces.IDataConverter(widget)
                value = converter.toFieldValue(widget.value)
                # validate field value
                zope.component.getMultiAdapter(
                    (self.context,
                     self.request,
                     self.parentForm,
                     getattr(widget, 'field', None),
                     widget),
                    interfaces.IValidator).validate(value)
            except (zope.schema.ValidationError, ValueError), error:
                # on exception, setup the widget error message
                view = zope.component.getMultiAdapter(
                    (error, self.request, widget, widget.field,
                     self.parentForm, self.context),
                    interfaces.IErrorViewSnippet)
                view.update()
                widget.error = view

    def setupFields(self):
        self.fields = Fields(self.__parent__.field.schema)

    def update(self, ignoreContext=None, setErrors=True):
        if self.__parent__.field is None:
            raise ValueError("%r .field is None, that's a blocking point" % self.__parent__)
        #update stuff from parent to be sure
        self.mode = self.__parent__.mode
        if ignoreContext is not None:
            self.ignoreContext = ignoreContext
        else:
            self.ignoreContext = self.__parent__.ignoreContext
        self.ignoreRequest = self.__parent__.ignoreRequest
        if interfaces.IFormAware.providedBy(self.__parent__):
            self.ignoreReadonly = self.parentForm.ignoreReadonly

        prefix = ''
        if self.parentForm:
            prefix = util.expandPrefix(self.parentForm.prefix) + \
                util.expandPrefix(self.parentForm.widgets.prefix)

        self.prefix = prefix+self.__parent__.field.__name__

        self.setupFields()

        super(ObjectSubForm, self).update()

        if setErrors:
            #hmmm, do we need this here? seems to be over-validated
            self._validate()

    def getContent(self):
        return self.__parent__._value

class ObjectConverter(BaseDataConverter):
    """Data converter for IObjectWidget."""

    zope.component.adapts(
        zope.schema.interfaces.IObject, interfaces.IObjectWidget)

    def toWidgetValue(self, value):
        """Just dispatch it."""
        if value is self.field.missing_value:
            return interfaces.NOVALUE

        retval = {}
        for name in zope.schema.getFieldNames(self.field.schema):
            dm = zope.component.getMultiAdapter(
                (value, self.field.schema[name]), interfaces.IDataManager)
            retval[name] = dm.query()

        return retval

    def createObject(self, value):
        #keep value passed, maybe some subclasses want it
        #value here is the raw extracted from the widget's subform
        #in the form of a dict key:fieldname, value:fieldvalue

        name = getIfName(self.field.schema)
        creator = zope.component.queryMultiAdapter(
            (self.widget.context, self.widget.request,
             self.widget.form, self.widget),
            interfaces.IObjectFactory,
            name=name)
        if creator:
            obj = creator(value)
        else:
            raise ValueError("No IObjectFactory adapter registered for %s" %
                             name)

        return obj

    def toFieldValue(self, value):
        """See interfaces.IDataConverter"""
        if value is interfaces.NOVALUE:
            return self.field.missing_value

        if self.widget.subform.ignoreContext:
            obj = self.createObject(value)
        else:
            dm = zope.component.getMultiAdapter(
                (self.widget.context, self.field), interfaces.IDataManager)
            try:
                obj = dm.get()
            except KeyError:
                obj = self.createObject(value)

        obj = self.field.schema(obj)

        names = []
        for name in zope.schema.getFieldNames(self.field.schema):
            try:
                dm = zope.component.getMultiAdapter(
                    (obj, self.field.schema[name]), interfaces.IDataManager)
                oldval = dm.query()
                if (oldval != value[name]
                    or zope.schema.interfaces.IObject.providedBy(
                        self.field.schema[name])
                    ):
                    dm.set(value[name])
                    names.append(name)
            except KeyError:
                pass

        if names:
            zope.event.notify(
                zope.lifecycleevent.ObjectModifiedEvent(obj,
                    zope.lifecycleevent.Attributes(self.field.schema, *names)))

        # Commonly the widget context is security proxied. This method,
        # however, should return a bare object, so let's remove the
        # security proxy now that all fields have been set using the security
        # mechanism.
        return removeSecurityProxy(obj)


class ObjectWidget(widget.Widget):
    zope.interface.implements(interfaces.IObjectWidget)

    subform = None
    _value = interfaces.NOVALUE
    _updating = False

    def _getForm(self, content):
        form = getattr(self, 'form', None)
        self.subform = zope.component.getMultiAdapter(
            (content, self.request,
             self.context,
             form, self, self.field),
            interfaces.ISubformFactory)()

    def updateWidgets(self, setErrors=True):
        if self._value is not interfaces.NOVALUE:
            self._getForm(self._value)
            ignore = None
        else:
            self._getForm(None)
            ignore = True

        self.subform.update(ignore, setErrors=setErrors)

    def update(self):
        #very-very-nasty: skip raising exceptions in extract while we're updating
        self._updating = True
        try:
            super(ObjectWidget, self).update()
            self.updateWidgets(setErrors=False)
        finally:
            self._updating = False

    @apply
    def value():
        """This invokes updateWidgets on any value change e.g. update/extract."""
        def get(self):
            return self.extract(setErrors=True)
        def set(self, value):
            self._value = value
            # ensure that we apply our new values to the widgets
            self.updateWidgets()
        return property(get, set)


    def extract(self, default=interfaces.NOVALUE, setErrors=True):
        if self.name+'-empty-marker' in self.request:
            self.updateWidgets(setErrors=False)

            value, errors = self.subform.extractData(setErrors=setErrors)

            if errors:
                #very-very-nasty: skip raising exceptions in extract
                #while we're updating
                if self._updating:
                    return default
                raise MultipleErrors(errors)

            return value

        else:
            return default


######## default adapters

class SubformAdapter(object):
    """Most basic-default subform factory adapter"""

    zope.interface.implements(interfaces.ISubformFactory)
    zope.component.adapts(zope.interface.Interface, interfaces.IFormLayer,
                          zope.interface.Interface,
                          zope.interface.Interface, interfaces.IObjectWidget,
                          zope.interface.Interface)

    factory = ObjectSubForm

    def __init__(self, context, request, widgetContext, form, widget, field):
        self.context = context
        self.request = request
        self.widgetContext = widgetContext
        self.form = form
        self.widget = widget
        self.field = field

    def __call__(self):
        #value is the extracted data from the form
        obj = self.factory(self.context, self.request, self.widget)
        return obj

    def __repr__(self):
        return '<%s %r>' % (self.__class__.__name__, self.__name__)

class FactoryAdapter(object):
    """Most basic-default object factory adapter"""

    zope.interface.implements(interfaces.IObjectFactory)
    zope.component.adapts(zope.interface.Interface, interfaces.IFormLayer,
        interfaces.IForm, interfaces.IWidget)

    factory = None

    def __init__(self, context, request, form, widget):
        self.context = context
        self.request = request
        self.form = form
        self.widget = widget

    def __call__(self, value):
        #value is the extracted data from the form
        obj = self.factory()
        zope.event.notify(zope.lifecycleevent.ObjectCreatedEvent(obj))
        return obj

    def __repr__(self):
        return '<%s %r>' % (self.__class__.__name__, self.__name__)

# XXX: Probably we should offer an register factrory method which allows to
# use all discriminators e.g. context, request, form, widget as optional
# arguments. But can probably do that later in a ZCML directive
def registerFactoryAdapter(for_, klass):
    """register the basic FactoryAdapter for a given interface and class"""
    name = getIfName(for_)
    class temp(FactoryAdapter):
        factory = klass
    zope.component.provideAdapter(temp, name=name)
