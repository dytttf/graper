#!/bin/bash
rm -rf build dist graper.egg-info
python setup.py sdist
python setup.py bdist_wheel
twine upload dist/*