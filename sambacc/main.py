#
# sambacc: a samba container configuration tool
# Copyright (C) 2021  John Mulligan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>
#

import argparse
import os
import sys
import time

import sambacc.config as config
import sambacc.netcmd_loader as nc
import sambacc.nsswitch_loader as nsswitch
import sambacc.passdb_loader as passdb
import sambacc.passwd_loader as ugl
import sambacc.paths as paths

DEFAULT_CONFIG = "/etc/samba/container/config.json"


class Fail(ValueError):
    pass


def print_config(cli, config):
    cfgs = cli.config or []
    iconfig = config.read_config_files(cfgs).get(cli.identity)
    nc.template_config(sys.stdout, iconfig)


def import_config(cli, config):
    # there are some expectations about what dirs exist and perms
    paths.ensure_samba_dirs()

    cfgs = cli.config or []
    iconfig = config.read_config_files(cfgs).get(cli.identity)
    loader = nc.NetCmdLoader()
    loader.import_config(iconfig)


def import_users(cli, config):
    """Import (locally defined) users and groups into the system
    config files and samba passdb. This enables the configured users
    to log into the smbd instance.
    """
    cfgs = cli.config or []
    iconfig = config.read_config_files(cfgs).get(cli.identity)
    etc_passwd_loader = ugl.PasswdFileLoader(cli.etc_passwd_path)
    etc_group_loader = ugl.GroupFileLoader(cli.etc_group_path)
    smb_passdb_loader = passdb.PassDBLoader()

    etc_passwd_loader.read()
    etc_group_loader.read()
    for u in iconfig.users():
        etc_passwd_loader.add_user(u)
    for g in iconfig.groups():
        etc_group_loader.add_group(g)
    etc_passwd_loader.write()
    etc_group_loader.write()

    for u in iconfig.users():
        smb_passdb_loader.add_user(u)
    return


def init_container(cli, config):
    """Run all of the standard set-up actions."""
    import_config(cli, config)
    import_users(cli, config)

    # should nsswitch validation/edit be conditional only on ads?
    nss = nsswitch.NameServiceSwitchLoader("/etc/nsswitch.conf")
    nss.read()
    if not nss.winbind_enabled():
        nss.ensure_winbind_enabled()
        nss.write()


def run_container(cli, config):
    if not getattr(cli, "no_init", False):
        init_container(cli, config)
    else:
        paths.ensure_samba_dirs()
    if cli.target == "smbd":
        # execute smbd process
        cmd = [
            "/usr/sbin/smbd",
            "--foreground",
            "--log-stdout",
            "--no-process-group",
        ]
        os.execvp(cmd[0], cmd)
    elif cli.target == "winbindd":
        if getattr(cli, "insecure_auto_join", False):
            join(cli, config)
        # execute winbind process
        cmd = [
            "/usr/sbin/winbindd",
            "--foreground",
            "--stdout",
            "--no-process-group",
        ]
        os.execvp(cmd[0], cmd)
    else:
        raise Fail(f"invalid target process: {cli.target}")


def join(cli, config):
    """Perform an insecure domain join.
    We call this insecure because the password must be passed to
    this tool in cleartext.
    This is only to be used for testing, not for production.
    """
    # maybe in the future we'll have more secure methods
    joiner = nc.Joiner()
    joiner.join(cli.username, cli.password)


default_cfunc = print_config


def from_env(ns, var, ename, default=None, vtype=str):
    value = getattr(ns, var, None)
    if not value:
        value = os.environ.get(ename, "")
    if vtype is not None:
        value = vtype(value)
    if value:
        setattr(ns, var, value)


def split_paths(value):
    if not value:
        return value
    if not isinstance(value, list):
        value = [value]
    out = []
    for v in value:
        for part in v.split(":"):
            out.append(part)
    return out


def pre_action(cli):
    """Handle debugging/diagnostic related options before the target
    action of the command is performed.
    """
    if cli.debug_delay:
        time.sleep(int(cli.debug_delay))


def main(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        action="append",
        help=(
            "Specify source configuration"
            " (can also be set in the environment by SAMBACC_CONFIG)."
        ),
    )
    parser.add_argument(
        "--identity",
        help=(
            "A string identifying the local identity"
            " (can also be set in the environment by SAMBA_CONTAINER_ID)."
        ),
    )
    parser.add_argument(
        "--etc-passwd-path",
        default="/etc/passwd",
        help="Specify a path for the passwd file.",
    )
    parser.add_argument(
        "--etc-group-path",
        default="/etc/group",
        help="Specify a path for the group file.",
    )
    parser.add_argument(
        "--username",
        default="Administrator",
        help="Specify a user name for domain access.",
    )
    parser.add_argument(
        "--password", default="", help="Specify a password for domain access."
    )
    parser.add_argument(
        "--debug-delay",
        type=int,
        help="Delay activity for a specified number of seconds.",
    )
    sub = parser.add_subparsers()
    p_print_config = sub.add_parser(
        "print-config",
        help=(
            "Display the samba configuration sourced from the sambacc config"
            " in the format of smb.conf."
        ),
    )
    p_print_config.set_defaults(cfunc=print_config)
    p_import = sub.add_parser(
        "import",
        help=(
            "Import configuration parameters from the sambacc config to"
            " samba's registry config."
        ),
    )
    p_import.set_defaults(cfunc=import_config)
    p_import_users = sub.add_parser(
        "import-users",
        help=(
            "Import users and groups from the sambacc config to the passwd"
            " and group files to support local (non-domain based) login."
        ),
    )
    p_import_users.set_defaults(cfunc=import_users)
    p_init = sub.add_parser(
        "init", help=("Initialize the entire container environment.")
    )
    p_init.set_defaults(cfunc=init_container)
    p_run = sub.add_parser("run", help=("Run a specified server process."))
    p_run.set_defaults(cfunc=run_container)
    p_run.add_argument(
        "--no-init",
        action="store_true",
        help=(
            "Do not initialize the container envionment."
            " Only start running the target process."
        ),
    )
    p_run.add_argument(
        "--insecure-auto-join",
        action="store_true",
        help=(
            "Perform an inscure domain join prior to starting a service."
            " Based on env vars JOIN_USERNAME and INSECURE_JOIN_PASSWORD."
        ),
    )
    p_run.add_argument(
        "target", choices=["smbd", "winbindd"], help="Which process to run"
    )
    p_join = sub.add_parser(
        "insecure-join",
        help=(
            "Perform a domain join. WARNING: Password is sourced from"
            " the CLI or ENV and is not secure. Use only for"
            " testing/non-production purposes."
        ),
    )
    p_join.set_defaults(cfunc=join)
    cli = parser.parse_args(args)
    from_env(
        cli,
        "config",
        "SAMBACC_CONFIG",
        vtype=split_paths,
        default=DEFAULT_CONFIG,
    )
    from_env(cli, "identity", "SAMBA_CONTAINER_ID")
    from_env(cli, "username", "JOIN_USERNAME")
    from_env(cli, "password", "INSECURE_JOIN_PASSWORD")

    if not cli.identity:
        raise Fail("missing container identity")

    pre_action(cli)
    cfunc = getattr(cli, "cfunc", default_cfunc)
    cfunc(cli, config)

    return


if __name__ == "__main__":
    main()
