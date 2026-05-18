###################################################################################################
#                                          TESTS                                                  #
###################################################################################################
test-unit:
	python -m pytest -s -v $$(find src -name 'test_*.py' -o -name '*_test_unit.py')

test-deep-research:
	python -m pytest -s -v src/deep_research/deep_research_test_unit.py
	