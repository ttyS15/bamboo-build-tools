# coding: utf-8

# $Id: $


import os.path
import re
import shutil
from subprocess import Popen, PIPE
import sys
from bamboo.helpers import cout, cerr, query_yes_no


class SVNError(Exception):
    pass


class SVNHelper(object):
    """ Работа с JIRA-задачами в SVN."""

    stable_dir = 'branches/stable'
    tags_dir = 'tags/release'
    commit_message_filename = 'commit-message.txt'

    def __init__(self, project_key, configfile='bamboo.cfg', root='^'):
        self.project_key = project_key
        self.project_root = root
        self.repo_url = 'http://y.rutube.ru/vrepo/'
        self.parse_config(configfile)

    def parse_config(self, configfile):
        filename = os.path.abspath(configfile)
        if os.path.exists(filename) and os.path.isfile(filename):
            config_locals = {}
            execfile(filename, locals=config_locals)
            self.__dict__.update(config_locals)

    def log_tasks(self, revision, branch='^/trunk'):
        args = ('log',)
        if revision:
            args += ('-r', '%s:HEAD' % revision)
        else:
            args += ('-l', '100')
        args += (branch,)
        cerr("Running svn client")
        stdout, stderr, return_code = self.svn(args)
        cerr("Collecting tasks")
        if return_code != 0:
            raise SVNError(stderr)

        tasks = dict()
        last_rev = None
        for line in stdout.splitlines():
            m = re.match(r'^r([\d]+) \|', line)
            if m:
                last_rev = int(m.group(1))
                continue
            m = re.match(r'^%s-([\d]+) (.*)' % self.project_key, line)
            if not m:
                continue
            task = int(m.group(1))
            message = m.group(2)
            tasks.setdefault(task, [])
            tasks[task].append((last_rev, message))
        return tasks

    def print_logged_tasks(self, tasks):
        task_keys = sorted(tasks.keys())
        cout("collected:", ','.join('%s-%s' % (self.project_key, t)
                                    for t in task_keys))
        cout()
        for task in task_keys:
            revisions = map(lambda item: str(item[0]), sorted(tasks[task]))
            cout('%s-%s: %s' % (self.project_key, task, ','.join(revisions)))

    def svn(self, args, quiet=False):
        args = ('/usr/bin/env', 'svn') + args
        return self.execute(args, quiet)

    def compute_stable_source(self, stable):
        parts = stable.split('.')
        if parts[1] == 'x':
            source = os.path.join(self.project_root, 'trunk')
            cerr("Major stable %s assumed created from %s" % (stable, source))
        elif parts[2] == 'x':
            source = os.path.join(self.project_root, self.stable_dir,
                                  '%s.x' % parts[0])
            cerr("Minor stable %s assumed created from %s" % (stable, source))
        else:
            source = os.path.join(self.project_root, self.stable_dir,
                                  '%s.%s.x' % (parts[0], parts[1]))
            cerr("Sub-minor stable %s assumed created from %s" % (
                stable, source))
        return source

    def check_dir_exists(self, path):
        args = ('info', path)
        cerr("Checking existance of %s" % path)
        stdout, stderr, return_code = self.svn(args, quiet=True)
        if return_code == 0:
            return True

    def svn_copy(self, source, destination, task, message=None,
                 interactive=False):
        message = message or '%s copy %s to %s' % (task, source, destination)
        args = ('cp', source, destination, '-m', message)
        if interactive:
            self.confirm_execution(args)
        cerr("Copying %s to %s" % (source, destination))
        stdout, stderr, return_code = self.svn(args)
        if return_code != 0:
            raise SVNError(stderr)

    def create_stable(self, stable, task, branch=None, interactive=False):
        stable_path = os.path.join(self.project_root, self.stable_dir)
        if not self.check_dir_exists(stable_path):
            self.makedir(stable_path, interactive=interactive)
        stable_path = os.path.join(stable_path, stable)
        if self.check_dir_exists(stable_path):
            cerr("Stable already exists")
            return

        source = self.compute_stable_source(stable)
        if branch:
            cerr('Source overriden from command line: %s' % branch)
            source = branch
        if not source.endswith('trunk'):
            if not self.check_dir_exists(source):
                raise SVNError("Source for stable does not exists")
        msg = '%s creating stable %s' % (task, stable)
        self.svn_copy(source, stable_path, task, message=msg,
                      interactive=interactive)

    def revert_working_copy(self):
        cerr('Cleaning working copy')
        args = ('revert', '-R', '.')
        stdout, stderr, return_code = self.svn(args)
        if return_code != 0:
            raise SVNError(stderr)

    def svn_update(self):
        cerr('Updating from SVN')
        args = ('up',)
        stdout, stderr, return_code = self.svn(args)
        if return_code != 0:
            raise SVNError(stderr)

    def check_collected_tasks(self, collected, tasks,
                              not_found_msg='These tasks not found in SVN log:',
                              found_msg='collected tasks:'):
        not_found = set(tasks) - set(collected)
        if not_found:
            cerr(not_found_msg, ','.join(not_found))
            cerr(found_msg, ','.join(collected))
            raise SVNError('not all tasks collected')

    def check_for_conflicts(self):
        cerr('Checking merge result')
        args = ('st',)
        stdout, stderr, return_code = self.svn(args)
        for line in stdout.splitlines():
            if 'C' in line[:8]:
                raise SVNError('Conflict found')
        if return_code != 0:
            raise SVNError(stderr)

    def confirm_execution(self, args):
        cerr('SVN command:')
        cerr('svn ' + ' '.join(args))
        if not query_yes_no('commit?', default='yes'):
            cerr('Aborted')
            sys.exit(0)

    def svn_commit(self, interactive):
        args = ('ci', '-F', self.commit_message_filename)
        if interactive:
            self.confirm_execution(args)
        cerr("Committing merge to SVN")
        stdout, stderr, return_code = self.svn(args)
        if return_code != 0:
            raise SVNError(stderr)

    def merge_tasks(self, task_key, tasks, branch='trunk', interactive=False):
        if not tasks:
            raise ValueError('No tasks requested')
        self.revert_working_copy()
        self.svn_update()
        with open(self.commit_message_filename, 'w+') as commit_msg_file:
            commit_msg_file.write(
                '%s merge tasks %s\n' % (task_key, ','.join(tasks)))
            source = os.path.join(self.project_root, branch)
            commit_msg_file.write('Request revisions from %s:\n' % source)
            logged = self.log_tasks(None, branch=source)
            collected = ['%s-%s' % (self.project_key, t) for t in logged.keys()]
            self.check_collected_tasks(collected, tasks)
            cerr('Merging with svn merge --non-interactive -c $REV')
            revisions = []
            for t, revs in logged.items():
                for r, msg in revs:
                    revisions.append((r, t, msg))
            revisions = sorted(revisions, key=lambda item: item[0])
            for r, t, msg in revisions:
                jira_task = '%s-%s' % (self.project_key, t)
                if jira_task not in tasks:
                    continue
                commit_msg_file.write('r%s %s %s\n' % (r, jira_task, msg))
                args = ('merge', '--non-interactive', '-c', 'r%s' % r, source)
                self.svn(args, quiet=True)
            commit_msg_file.flush()
            commit_msg_file.seek(0)
            merged = []
            for line in commit_msg_file.readlines():
                m = re.match(r'^r[\d]+ ([A-Z]+-[\d]+)', line)
                if not m:
                    continue
                merged.append(m.group(1))
            self.check_collected_tasks(merged, tasks,
                                       not_found_msg='These tasks not merged:',
                                       found_msg='Merged tasks:')

        self.check_for_conflicts()

        self.svn_commit(interactive)

    def get_last_tag(self, tags_dir):
        args = ('ls', tags_dir)
        stdout, stderr, return_code = self.svn(args, quiet=True)
        tags = stdout.splitlines()
        last_tag = 0 if not tags else int(tags[-1].strip('/'))
        return last_tag

    def release(self, task_key, release, interactive=False):
        cerr("Checking for tags dir existance")
        released_tags = os.path.join(self.project_root, self.tags_dir, release)
        if not self.check_dir_exists(released_tags):
            cerr("Creating tags dir")
            self.makedir(released_tags, interactive=interactive)
        last_tag = self.get_last_tag(released_tags)
        new_tag = '%02d' % (last_tag + 1)
        tag = os.path.join(released_tags, new_tag)
        stable = self.compute_stable(release)
        msg = '%s create tag %s-%s' % (task_key, release, new_tag)
        self.svn_copy(stable, tag, task_key, message=msg,
                      interactive=interactive)

    def makedir(self, path, interactive=False):
        args = ('mkdir', '--parents', path, '')
        if interactive:
            self.confirm_execution(args)
        stdout, stderr, return_code = self.svn(args)
        if return_code != 0:
            raise SVNError(stderr)

    def compute_stable(self, release):
        stable = release.replace('0', 'x')
        stable = re.sub(r'x\.(x|[\d]+)$', 'x', stable)
        return os.path.join(self.project_root, self.stable_dir, stable)

    def build(self, release, interactive=False):
        released_tags = os.path.join(self.project_root, self.tags_dir, release)
        tag = self.get_last_tag(released_tags)
        remote = os.path.join(released_tags, str(tag))
        package_name = '%s-%s-%s' % (self.project_key, release, tag)
        local_path = os.path.join('/tmp', package_name)
        if os.path.exists(local_path):
            if not interactive or query_yes_no('remove %s?' % local_path,
                                               default='yes'):
                shutil.rmtree(local_path)
            else:
                cerr('Aborted')
                sys.exit(0)
        self.export(remote, local_path)
        archive_name = '/tmp/%s.tgz' % package_name
        self.tar(archive_name, '/tmp', package_name, quiet=True)
        dest = os.path.join(self.repo_url, self.project_key)
        self.upload(archive_name, dest, interactive=interactive)
        shutil.rmtree(local_path)
        os.unlink(archive_name)

    def tar(self, archive, chdir, folder, quiet=False):
        args = ('/usr/bin/env', 'tar', 'czf', archive, '-C', chdir, folder)
        return self.execute(args, quiet)

    def execute(self, args, quiet=False):
        if not quiet:
            sys.stderr.write(' '.join(args[1:]) + '\n')
        p = Popen(args, stdout=PIPE, stderr=PIPE)
        stdout, stderr = p.communicate()
        return stdout, stderr, p.returncode

    def upload(self, source, destination, quiet=False, interactive=False):
        args = ('/usr/bin/env', 'curl', '-T', source, destination)
        cerr("Upload command: %s" % ' '.join(args[1:]))
        if interactive and not query_yes_no('upload file?', default='yes'):
            cerr('Aborted')
            return
        return self.execute(args, quiet)

    def checkout(self, remote, local):
        args = ('co', remote, local)
        stdout, stderr, return_code = self.svn(args)
        if return_code != 0:
            raise SVNError(stderr)

    def export(self, remote, local):
        args = ('export', remote, local)
        stdout, stderr, return_code = self.svn(args)
        if return_code != 0:
            raise SVNError(stderr)

