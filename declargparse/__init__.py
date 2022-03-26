# -*- mode: python; -*-
import argparse
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial, reduce
from itertools import chain
import os
import re
import sys
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
)


class EnvVar:
    name: str
    value: Optional[str]

    def __init__(self, name: str):
        self.name = name
        self.value = os.getenv(name)

    def __iter__(self) -> Iterator[Optional[str]]:
        yield self.name
        yield self.value


class ArgSpec:
    args: Tuple[str, ...]
    kwargs: Dict[str, Any]

    def __init__(self, *args: str, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs

    @property
    def dest(self) -> str:
        return self.makeaction().dest

    @property
    def option_strings(self) -> Sequence[str]:
        return self.makeaction().option_strings

    @property
    def required(self) -> bool:
        return bool(self.kwargs.get("required"))

    @property
    def envvarbase(self) -> str:
        return self.dest.upper()

    def makeaction(self) -> argparse.Action:
        parser = argparse.ArgumentParser()
        return parser.add_argument(*self.args, **self.kwargs)

    def construct_envvar(self, envvar_prefix: str) -> EnvVar:
        return EnvVar(f"{envvar_prefix}_{self.envvarbase}")

    def enhance_kwargs(
        self, envvar_prefix: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return enhanced keyword arguments.

        This method makes the following changes to keyword arguments:

        - Disallow an option with a default value to also be required,
          since providing a default value implies that supplying the
          option isn't necessary.

        - Annotate help string of required variables.

        - If an environment variable prefix is provided, then
          construct the environment variable and add it to the help
          string.

        - If an environment variable prefix and a default value are
          provided, and if the argument's environment variable is set,
          then replace the default value with the value of the
          environment variable.

        """
        if envvar_prefix is not None:
            envvar = self.construct_envvar(envvar_prefix)
        else:
            envvar = None

        transformations = [
            (
                partial(drop_key, "required")
                if self.kwargs.get("required")
                and ("default" in self.kwargs or envvar is not None)
                else None
            ),
            (
                partial(annotate_helpstr, "required")
                if self.kwargs.get("required")
                else None
            ),
            (
                partial(
                    annotate_helpstr, f"environment variable: {envvar.name}"
                )
                if envvar is not None and "help" in self.kwargs
                else None
            ),
            (
                partial(replace_default, envvar.value)
                if envvar is not None and envvar.value is not None
                else None
            ),
        ]
        return dict(
            reduce(
                lambda x, f: f(x) if f is not None else x,  # type: ignore
                transformations,
                self.kwargs.items(),
            )
        )


def drop_key(
    key: str,
    pairs: Iterable[Tuple[str, Any]],
) -> Iterator[Tuple[str, Any]]:
    """Drop pairs in which the first element is 'required'."""
    return filter(lambda t: t[0] != key, pairs)


def annotate_helpstr(
    annotation: str,
    pairs: Iterable[Tuple[str, Any]],
) -> Iterator[Tuple[str, Any]]:
    """Add annotation to second element if first element is 'help'."""

    def f(pair: Tuple[str, Any]):
        if pair[0] == "help" and isinstance(pair[1], str):
            return (
                pair[0],
                add_annotation_to_helpstr(annotation, pair[1]),
            )
        else:
            return pair

    return map(f, pairs)


def replace_default(
    defaultvalue: Any,
    pairs: Iterable[Tuple[str, Any]],
) -> Iterator[Tuple[str, Any]]:
    """Drop default from pairs and then add with given default value."""
    return chain(drop_key("default", pairs), [("default", defaultvalue)])


def add_annotation_to_helpstr(annotation: str, helpstr: str) -> str:
    """Add a parenthesized annotation to a help string.

    >>> add_annotation_to_helpstr('required', 'apply option')
    'apply option (required)'
    >>> add_annotation_to_helpstr(
    ...     'required',
    ...     'apply option (default: 1, env var: APP_OPT)',
    ... )
    'apply option (default: 1, env var: APP_OPT, required)'

    """
    match = re.search(r"\((.*)\)$", helpstr)
    if match:
        pattrs = match.group(1).split(", ")
        newattrstr = ", ".join(pattrs + [annotation])
        enhancedhelpstr = re.sub(r"\(.*\)$", f"({newattrstr})", helpstr)
    else:
        enhancedhelpstr = f"{helpstr} ({annotation})"
    return enhancedhelpstr


class SubcmdSpec:
    name: str
    parserspec: Dict[str, Any]
    subcmdfn: Union[  # NOTE: Workaround for python/mypy#708.
        Callable[[argparse.Namespace], int],
        Callable[[argparse.Namespace], int],
    ]
    argspecs: List[ArgSpec]

    def __init__(
        self,
        name: str,
        subcmdfn: Callable[[argparse.Namespace], int],
        argspecs: Optional[Sequence[ArgSpec]] = None,
        **parserspec: Any,
    ) -> None:
        self.name = name
        self.parserspec = dict(parserspec)
        self.subcmdfn = subcmdfn
        self.argspecs = list(argspecs) if argspecs is not None else []


class SubcmdGroup:
    groupspec: Dict[str, Any]
    subcmdspecs: List[SubcmdSpec]
    subcmdfnname: ClassVar[str] = "_subcmd"

    def __init__(
        self,
        subcmdspecs: Sequence[SubcmdSpec],
        **groupspec: Any,
    ) -> None:
        self.groupspec = dict(groupspec)
        self.subcmdspecs = list(subcmdspecs)


@dataclass
class CliSpec:
    parserspec: Dict[str, Any]
    argspecs: List[ArgSpec] = field(default_factory=list)
    subcmdgroup: Optional[SubcmdGroup] = None
    reject_unknown_args: bool = True
    envvar_prefix: Optional[str] = None

    @property
    def argnames(self) -> Tuple[str, ...]:
        return tuple(s.dest for s in self.argspecs)

    def tuplefromargs(self, args: argparse.Namespace) -> Tuple[Any, ...]:
        return tuple(getattr(args, name) for name in self.argnames)

    def namevaluepairsfromargs(
        self, args: argparse.Namespace
    ) -> Iterator[Tuple[str, Any]]:
        return zip(self.argnames, self.tuplefromargs(args))

    def makeparser(self) -> argparse.ArgumentParser:
        def add_arg_to_parser(
            parser: argparse.ArgumentParser,
            argspec: ArgSpec,
        ) -> argparse.ArgumentParser:
            """Add an argument specification to a parser.

            This function modifies and returns the parser argument.

            """
            _ = parser.add_argument(
                *argspec.args, **argspec.enhance_kwargs(self.envvar_prefix)
            )
            return parser

        parser = reduce(
            add_arg_to_parser,
            self.argspecs,
            argparse.ArgumentParser(**self.parserspec),
        )
        if self.subcmdgroup is not None:
            self._add_subcmdgroup_to_parser(self.subcmdgroup, parser)
        return parser

    def _add_subcmdgroup_to_parser(
        self,
        subcmdgroup: SubcmdGroup,
        parser: argparse.ArgumentParser,
    ) -> None:
        subparsers_action = parser.add_subparsers(**subcmdgroup.groupspec)
        for scs in subcmdgroup.subcmdspecs:
            parser = subparsers_action.add_parser(scs.name, **scs.parserspec)
            parser.set_defaults(**{subcmdgroup.subcmdfnname: scs.subcmdfn})
            for argspec in scs.argspecs:
                parser.add_argument(
                    *argspec.args, **argspec.enhance_kwargs(self.envvar_prefix)
                )

    def parseargs(
        self,
        arg: Optional[Iterable[str]] = None,
    ) -> argparse.Namespace:
        parser = self.makeparser()
        if self.reject_unknown_args:
            args = parser.parse_args()
        else:
            args, _ = parser.parse_known_args()
        self.validateargs(args)
        return args

    def validateargs(self, args: argparse.Namespace) -> None:
        missingargs = list(
            map(
                lambda t: t[0].option_strings,
                filter(
                    lambda t: t[0].required and t[1] is None,
                    zip(self.argspecs, self.tuplefromargs(args)),
                ),
            )
        )
        if missingargs:
            print(
                "error: the following arguments are required: "
                + ", ".join(
                    L[0] if len(L) == 1 else str(L) for L in missingargs
                ),
                file=sys.stderr,
            )
            sys.exit(2)

    def getenvvars(self, required: Optional[bool] = None) -> List[EnvVar]:
        if self.envvar_prefix is not None:
            envvars = [
                s.construct_envvar(self.envvar_prefix)
                for s in self.argspecs
                if required is None or s.required == required
            ]
        else:
            envvars = []
        return envvars

    def format_help(self) -> str:
        return self.makeparser().format_help()


def fromisoformat(s: str) -> datetime:
    """Return datetime from ISO 8601 string.

    >>> fromisoformat("2000-01-01T12:34:56+00:00")
    datetime.datetime(2000, 1, 1, 12, 34, 56, tzinfo=datetime.timezone.utc)

    """
    return datetime.strptime(
        re.sub(r":(\d\d)$", r"\1", s), "%Y-%m-%dT%H:%M:%S%z"
    )
