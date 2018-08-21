from django.conf import settings
from django.db import models
from django.urls import reverse


class ProjectManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Project(models.Model):
    """ Represents a project in a python project hierarchy """

    name = models.CharField(max_length=200, unique=True)

    objects = ProjectManager()

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)

    def get_absolute_url(self):
        return reverse('project-detail', kwargs={'package': self.name})


class ProjectVersionManager(models.Manager):

    def get_by_natural_key(self, name, version_number):
        return self.get(
            project=Project.objects.get_by_natural_key(name=name),
            version_number=version_number
            )

    def get_latest(self, name):
        return self.order_by('-sortable_version_number')[0]


class ProjectVersion(models.Model):
    """ Represents a particular version of a project in a python project hierarchy """

    project = models.ForeignKey(Project, models.CASCADE)
    version_number = models.CharField(max_length=200)
    sortable_version_number = models.CharField(max_length=200, blank=True)

    objects = ProjectVersionManager()

    class Meta:
        unique_together = ('project', 'version_number')
        ordering = ('-sortable_version_number',)

    def __str__(self):
        return self.project.name + " " + self.version_number

    def save(self, *args, **kwargs):
        if not self.sortable_version_number:
            self.sortable_version_number = self.generate_sortable_version_number()
        return super(ProjectVersion, self).save(*args, **kwargs)

    def natural_key(self):
        return self.project.natural_key() + (self.version_number,)
    natural_key.dependencies = ['cbv.Project']

    def get_absolute_url(self):
        return reverse('version-detail', kwargs={
            'package': self.project.name,
            'version': self.version_number,
        })

    @property
    def docs_version_number(self):
        return '.'.join(self.version_number.split('.')[:2])

    def generate_sortable_version_number(self):
        return "".join(part.zfill(2) for part in self.version_number.split("."))


class ModuleManager(models.Manager):
    def get_by_natural_key(self, module_name, project_name, version_number):
        return self.get(
            name=module_name,
            project_version=ProjectVersion.objects.get_by_natural_key(
                name=project_name,
                version_number=version_number)
            )

class Module(models.Model):
    """ Represents a module of a python project """

    project_version = models.ForeignKey(ProjectVersion, models.CASCADE)
    name = models.CharField(max_length=200)
    docstring = models.TextField(blank=True, default='')
    filename = models.CharField(max_length=511, default='')

    objects = ModuleManager()

    class Meta:
        unique_together = ('project_version', 'name')

    def __str__(self):
        return self.name

    def short_name(self):
        return self.name.split('.')[-1]

    def long_name(self):
        short_name = self.short_name()
        source_name = self.source_name()
        if short_name.lower() == source_name.lower():
            return short_name
        return f'{source_name} {short_name}'

    def source_name(self):
        name = self.name
        while name:
            try:
                return settings.CBV_SOURCES[name]
            except KeyError:
                name = '.'.join(name.split('.')[:-1])

    def natural_key(self):
        return (self.name,) + self.project_version.natural_key()
    natural_key.dependencies = ['cbv.ProjectVersion']

    def get_absolute_url(self):
        return reverse('module-detail', kwargs={
            'package': self.project_version.project.name,
            'version': self.project_version.version_number,
            'module': self.name,
        })


class KlassManager(models.Manager):
    def get_by_natural_key(self, klass_name, module_name, project_name, version_number):
        return self.get(
            name=klass_name,
            module=Module.objects.get_by_natural_key(
                module_name=module_name,
                project_name=project_name,
                version_number=version_number)
            )

    def get_latest_for_name(self, klass_name, project_name):
        qs = self.filter(
            name=klass_name,
            module__project_version__project__name=project_name,
        ) or self.filter(
            name__iexact=klass_name,
            module__project_version__project__name__iexact=project_name,
        )
        try:
            obj = qs.order_by('-module__project_version__sortable_version_number',)[0]
        except IndexError:
            raise self.model.DoesNotExist
        else:
            return obj


# TODO: quite a few of the methods on here should probably be denormed.
class Klass(models.Model):
    """ Represents a class in a module of a python project hierarchy """

    module = models.ForeignKey(Module, models.CASCADE)
    name = models.CharField(max_length=200)
    docstring = models.TextField(blank=True, default='')
    line_number = models.IntegerField()
    import_path = models.CharField(max_length=255)
    # because docs urls differ between Django versions
    docs_url = models.URLField(max_length=255, default='')

    objects = KlassManager()

    class Meta:
        unique_together = ('module', 'name')
        ordering = ('module__name', 'name')

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,) + self.module.natural_key()
    natural_key.dependencies = ['cbv.Module']

    def is_secondary(self):
        return (self.name.startswith('Base') or
                self.name.endswith('Base') or
                self.name.endswith('Mixin') or
                self.name.endswith('Error') or
                self.name == 'ProcessFormView')

    def get_absolute_url(self):
        return reverse('klass-detail', kwargs={
            'package': self.module.project_version.project.name,
            'version': self.module.project_version.version_number,
            'module': self.module.name,
            'klass': self.name
        })

    def get_source_url(self):
        url = 'https://github.com/django/django/blob/'
        version = self.module.project_version.version_number
        path = self.module.filename
        line = self.line_number
        return f'{url}{version}{path}#L{line}'

    def get_ancestors(self):
        if not hasattr(self, '_ancestors'):
            self._ancestors = Klass.objects.filter(inheritance__child=self).order_by('inheritance__order')
        return self._ancestors

    def get_children(self):
        if not hasattr(self, '_descendants'):
            self._descendants = Klass.objects.filter(ancestor_relationships__parent=self).order_by('name')
        return self._descendants

    #TODO: This is all mucho inefficient. Perhaps we should use mptt for
    #       get_all_ancestors, get_all_children, get_methods, & get_attributes?
    def get_all_ancestors(self):
        if not hasattr(self, '_all_ancestors'):
            # Get immediate ancestors.
            ancestors = self.get_ancestors().select_related('module__project_version__project')

            # Flatten ancestors and their forebears into a list.
            tree = []
            for ancestor in ancestors:
                tree.append(ancestor)
                tree += ancestor.get_all_ancestors()

            # Remove duplicates, leaving the last occurence in tact.
            # This is how python's MRO works.
            cleaned_ancestors = []
            for ancestor in reversed(tree):
                if ancestor not in cleaned_ancestors:
                    cleaned_ancestors.insert(0, ancestor)

            # Cache the result on this object.
            self._all_ancestors = cleaned_ancestors
        return self._all_ancestors

    def get_all_children(self):
        if not hasattr(self, '_all_descendants'):
            children = self.get_children().select_related('module__project_version__project')
            for child in children:
                children = children | child.get_all_children()
            self._all_descendants = children
        return self._all_descendants

    def get_methods(self):
        if not hasattr(self, '_methods'):
            methods = self.method_set.all().select_related('klass')
            for ancestor in self.get_all_ancestors():
                methods = methods | ancestor.get_methods()
            self._methods = methods
        return self._methods

    def get_attributes(self):
        if not hasattr(self, '_attributes'):
            attrs = self.attribute_set.all()
            for ancestor in self.get_all_ancestors():
                attrs = attrs | ancestor.get_attributes()
            self._attributes = attrs
        return self._attributes

    def get_prepared_attributes(self):
        attributes = self.get_attributes()
        # Make a dictionary of attributes based on name
        attribute_names = {}
        for attr in attributes:
            try:
                attribute_names[attr.name] += [attr]
            except KeyError:
                attribute_names[attr.name] = [attr]

        ancestors = self.get_all_ancestors()

        # Find overridden attributes
        for name, attrs in attribute_names.items():
            # Skip if we have only one attribute.
            if len(attrs) == 1:
                continue

            # Sort the attributes by ancestors.
            def _key(a):
                try:
                    # If ancestor, return the index (>= 0)
                    return ancestors.index(a.klass)
                except:
                    # else a.klass == self, so return -1
                    return -1
            sorted_attrs = sorted(attrs, key=_key)

            # Mark overriden KlassAttributes
            for a in sorted_attrs[1:]:
                a.overridden = True
        return attributes

    def basic_yuml_data(self, first=False):
        if hasattr(self, '_basic_yuml_data'):
            return self._basic_yuml_data
        yuml_data = []
        template = '[{parent}{{bg:{parent_col}}}]^-[{child}{{bg:{child_col}}}]'
        for ancestor in self.get_ancestors():
            yuml_data.append(template.format(
                parent=ancestor.name,
                child=self.name,
                parent_col='white' if ancestor.is_secondary() else 'lightblue',
                child_col='green' if first else 'white' if self.is_secondary() else 'lightblue',
            ))
            yuml_data += ancestor.basic_yuml_data()
        self._basic_yuml_data = yuml_data
        return self._basic_yuml_data

    def basic_yuml_url(self):
        data = ', '.join(self.basic_yuml_data(first=True))
        if not data:
            return None
        return f'http://yuml.me/diagram/plain;/class/{data}.svg'


class Inheritance(models.Model):
    """ Represents the inheritance relationships for a Klass """

    parent = models.ForeignKey(Klass, models.CASCADE)
    child = models.ForeignKey(Klass, models.CASCADE, related_name='ancestor_relationships')
    order = models.IntegerField()

    class Meta:
        ordering = ('order',)
        unique_together = ('child', 'order')

    def __str__(self):
        return f'{self.parent} <- {self.child} ({self.order})'


class KlassAttribute(models.Model):
    """ Represents an attribute on a Klass """

    klass = models.ForeignKey(Klass, models.CASCADE, related_name='attribute_set')
    name = models.CharField(max_length=200)
    value = models.CharField(max_length=511)
    line_number = models.IntegerField()

    class Meta:
        ordering = ('name',)
        unique_together = ('klass', 'name')

    def __str__(self):
        return f'{self.name} = {self.value}'


class ModuleAttribute(models.Model):
    """ Represents an attribute on a Module """

    module = models.ForeignKey(Module, models.CASCADE, related_name='attribute_set')
    name = models.CharField(max_length=200)
    value = models.CharField(max_length=511)
    line_number = models.IntegerField()

    class Meta:
        ordering = ('name',)
        unique_together = ('module', 'name')

    def __str__(self):
        return f'{self.name} = {self.value}'


class Method(models.Model):
    """ Represents a method on a Klass """

    klass = models.ForeignKey(Klass, models.CASCADE)
    name = models.CharField(max_length=200)
    docstring = models.TextField(blank=True, default='')
    code = models.TextField()
    kwargs = models.CharField(max_length=200)
    line_number = models.IntegerField()

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('name',)
