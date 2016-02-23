import filecmp
import os
import tempfile
from test import helpers
import unittest
import uuid

from scripttest import TestFileEnvironment
try:
    from urllib.parse import urljoin
except ImportError:
    from urlparse import urljoin


class GenomeSpaceShellTestCase(unittest.TestCase):

    def _call_shell_command(self, command, *args):
        env = TestFileEnvironment('./test-output')
        main_args = [
            "{0}/../genomespaceclient/shell.py".format(
                os.path.dirname(__file__)),
            "-u", helpers.get_test_username(),
            "-p", helpers.get_test_password(),
            command] + list(args)
        return env.run("python", *main_args, expect_stderr=True)

    def _get_test_file(self):
        return os.path.join(os.path.dirname(__file__), 'fixtures/logo.png')

    def _get_temp_file(self):
        return os.path.join(tempfile.gettempdir(), str(uuid.uuid4()))

    def test_list(self):
        local_test_file = self._get_test_file()

        self._call_shell_command(
            "cp", local_test_file,
            urljoin(helpers.get_test_folder(), 'list_logo.png'))
        output = self._call_shell_command(
            "ls", helpers.get_test_folder())
        self.assertTrue(
            'list_logo.png' in output.stdout,
            "Expected file not found. Received: %s" %
            (output,))

    def test_copy(self):
        local_test_file = self._get_test_file()
        local_temp_file = self._get_temp_file()

        self._call_shell_command(
            "cp", local_test_file,
            urljoin(helpers.get_test_folder(), 'logo.png'))

        self._call_shell_command(
            "cp", urljoin(helpers.get_test_folder(), 'logo.png'),
            local_temp_file)

        self.assertTrue(filecmp.cmp(local_test_file, local_temp_file))
        os.remove(local_temp_file)

    def test_move(self):
        local_test_file = self._get_test_file()
        local_temp_file = self._get_temp_file()

        output = self._call_shell_command(
            "ls", helpers.get_test_folder())
        if 'list_logo.png' in output.stdout:
            self._call_shell_command(
                "rm", urljoin(helpers.get_test_folder(), 'logo_copy.png'))

        self._call_shell_command(
            "cp", local_test_file,
            urljoin(helpers.get_test_folder(), 'logo.png'))

        self._call_shell_command(
            "mv", urljoin(helpers.get_test_folder(), 'logo.png'),
            urljoin(helpers.get_test_folder(), 'logo_copy.png'))

        self._call_shell_command(
            "cp", urljoin(helpers.get_test_folder(), 'logo_copy.png'),
            local_temp_file)

        self.assertTrue(filecmp.cmp(local_test_file, local_temp_file))
        os.remove(local_temp_file)

    def test_delete(self):
        local_test_file = self._get_test_file()
        self._call_shell_command(
            "cp", local_test_file,
            urljoin(helpers.get_test_folder(), 'delete_logo.png'))
        self._call_shell_command(
            "rm", urljoin(helpers.get_test_folder(), 'delete_logo.png'))