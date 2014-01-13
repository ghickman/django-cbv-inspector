import logging
import os
import subprocess
import sys

from utils import build_url, render_to_template


log = logging.getLogger('ccbv')


def build_module_page(version, module, classes):
    context = {
        'module': module,
        'classes': classes,
        'base_url': build_url(version=version, module=module),
    }
    path = os.path.join(version, module)
    render_to_template('module_list.html', context, path)


def build_klass_page(details, version, path):
    context = {'klass': details}
    build_path = os.path.join(version, *path.rsplit('.', 1))
    render_to_template('klass_detail.html', context, build_path)


def checkout_release(path, release):
    pwd = os.path.dirname(os.path.realpath(__file__))
    command = '{}/checkout_release.sh {} {}'.format(pwd, path, release)
    try:
        subprocess.check_call(command, shell=True, stdout=open(os.devnull, 'w'))
    except subprocess.CalledProcessError:
        log.error('An error occurred trying to run `{}`.\n'.format(command))
        sys.exit(1)
