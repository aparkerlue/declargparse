# -*- mode: makefile-gmake; -*-
name := $(shell sed -n 's/^name *= *"\([^"]\+\)"/\1/p' pyproject.toml)
version := $(shell sed -n 's/^version *= *"\([^"]\+\)"/\1/p' pyproject.toml)
tag := py3-none-any
target := dist/$(name)-$(version)-$(tag).whl

package := declargparse
sources := $(wildcard $(package)/*.py)

POETRY := poetry
BLACK := black
FLAKE8 := flake8
MYPY := $(POETRY) run mypy
PYTEST := $(POETRY) run pytest

.PHONY : all
all : $(target)

.PHONY : check
check : pyproject.toml $(sources)
	$(POETRY) check
	$(MYPY) -p $(package)
	$(FLAKE8) $(filter-out $<,$^)
	$(BLACK) --check $(filter-out $<,$^)
	$(PYTEST) --pyargs --doctest-modules $(package)
	! grep -R '#.*\bFIXME\b' $(filter-out $<,$^)

.PHONY : clean
clean :
	-rm -r dist/

.PHONY : distclean
distclean : clean
	-rm -r .mypy_cache/ .pytest_cache/ __pycache__/

$(target) : pyproject.toml $(sources)
	$(POETRY) build -f wheel
