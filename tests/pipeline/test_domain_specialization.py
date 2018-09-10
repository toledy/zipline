from textwrap import dedent

import pandas as pd

from zipline.pipeline import Pipeline
from zipline.pipeline.data import Column, DataSet
from zipline.pipeline.data.testing import TestingDataSet
from zipline.pipeline.domain import (
    AmbiguousDomain,
    CA_EQUITIES,
    GENERIC,
    infer_domain,
    GB_EQUITIES,
    US_EQUITIES,
)
from zipline.pipeline.factors import CustomFactor
import zipline.testing.fixtures as zf
from zipline.testing.core import parameter_space, powerset
from zipline.testing.predicates import assert_equal, assert_messages_equal


class Sum(CustomFactor):

    def compute(self, today, assets, out, data):
        out[:] = data.sum(axis=0)

    @classmethod
    def create(cls, column, window_length):
        return cls(inputs=[column], window_length=window_length)


class MixedGenericsTestCase(zf.WithSeededRandomPipelineEngine,
                            zf.ZiplineTestCase):
    START_DATE = pd.Timestamp('2014-01-02', tz='utc')
    END_DATE = pd.Timestamp('2014-01-31', tz='utc')
    ASSET_FINDER_EQUITY_SIDS = (1, 2, 3, 4, 5)
    ASSET_FINDER_COUNTRY_CODE = 'US'

    def test_mixed_generics(self):
        """
        Test that we can run pipelines with mixed generic/non-generic terms.

        This test is a regression test for failures encountered during
        development where having a mix of generic and non-generic columns in
        the term graph caused bugs in our extra row accounting.
        """
        USTestingDataSet = TestingDataSet.specialize(US_EQUITIES)
        base_terms = {
            'sum3_generic': Sum.create(TestingDataSet.float_col, 3),
            'sum3_special': Sum.create(USTestingDataSet.float_col, 3),
            'sum10_generic': Sum.create(TestingDataSet.float_col, 10),
            'sum10_special': Sum.create(USTestingDataSet.float_col, 10),
        }

        def run(ts):
            pipe = Pipeline(ts, domain=US_EQUITIES)
            start = self.trading_days[-5]
            end = self.trading_days[-1]
            return self.run_pipeline(pipe, start, end)

        base_result = run(base_terms)

        for subset in powerset(base_terms):
            subset_terms = {t: base_terms[t] for t in subset}
            result = run(subset_terms).sort_index(axis=1)
            expected = base_result[list(subset)].sort_index(axis=1)
            assert_equal(result, expected)


class SpecializeTestCase(zf.ZiplineTestCase):

    @parameter_space(domain=[US_EQUITIES, CA_EQUITIES, GB_EQUITIES])
    def test_specialize(self, domain):
        class MyData(DataSet):
            col1 = Column(dtype=float)
            col2 = Column(dtype=int, missing_value=100)
            col3 = Column(dtype=object, missing_value="")

        class MyDataSubclass(MyData):
            col4 = Column(dtype=float)

        def do_checks(cls, colnames):

            specialized = cls.specialize(domain)

            # Specializations should be memoized.
            self.assertIs(specialized, cls.specialize(domain))

            # Specializations should have the same name.
            assert_equal(specialized.__name__, cls.__name__)
            self.assertIs(specialized.domain, domain)

            for attr in colnames:
                original = getattr(cls, attr)
                new = getattr(specialized, attr)

                # We should get a new column from the specialization, which
                # should be the same object that we would get from specializing
                # the original column.
                self.assertIsNot(original, new)
                self.assertIs(new, original.specialize(domain))

                # Columns should be bound to their respective datasets.
                self.assertIs(original.dataset, cls)
                self.assertIs(new.dataset, specialized)

                # The new column should have the domain of the specialization.
                assert_equal(new.domain, domain)

                # Names, dtypes, and missing_values should match.
                assert_equal(original.name, new.name)
                assert_equal(original.dtype, new.dtype)
                assert_equal(original.missing_value, new.missing_value)

        do_checks(MyData, ['col1', 'col2', 'col3'])
        do_checks(MyDataSubclass, ['col1', 'col2', 'col3', 'col4'])

    @parameter_space(domain=[US_EQUITIES, CA_EQUITIES, GB_EQUITIES])
    def test_unspecialize(self, domain):

        class MyData(DataSet):
            col1 = Column(dtype=float)
            col2 = Column(dtype=int, missing_value=100)
            col3 = Column(dtype=object, missing_value="")

        class MyDataSubclass(MyData):
            col4 = Column(dtype=float)

        def do_checks(cls, colnames):
            specialized = cls.specialize(domain)
            unspecialized = specialized.unspecialize()
            specialized_again = unspecialized.specialize(domain)

            self.assertIs(unspecialized, cls)
            self.assertIs(specialized, specialized_again)

            for attr in colnames:
                original = getattr(cls, attr)
                new = getattr(specialized, attr)
                # Unspecializing a specialization should give back the
                # original.
                self.assertIs(new.unspecialize(), original)
                # Specializing again should give back the same as the first
                # specialization.
                self.assertIs(new.unspecialize().specialize(domain), new)

        do_checks(MyData, ['col1', 'col2', 'col3'])
        do_checks(MyDataSubclass, ['col1', 'col2', 'col3', 'col4'])

    @parameter_space(domain_param=[US_EQUITIES, CA_EQUITIES])
    def test_specialized_root(self, domain_param):
        different_domain = GB_EQUITIES

        class MyData(DataSet):
            domain = domain_param
            col1 = Column(dtype=float)

        class MyDataSubclass(MyData):
            col2 = Column(dtype=float)

        def do_checks(cls, colnames):
            # DataSets with concrete domains can't be specialized to other
            # concrete domains.
            with self.assertRaises(ValueError):
                cls.specialize(different_domain)

            # Same goes for columns of the dataset.
            for name in colnames:
                col = getattr(cls, name)
                with self.assertRaises(ValueError):
                    col.specialize(different_domain)

            # We always allow unspecializing to simplify the implementation of
            # loaders and dispatchers that want to use the same loader for an
            # entire dataset family.
            generic_non_root = cls.unspecialize()

            # Allow specializing a generic non-root back to its family root.
            self.assertIs(generic_non_root.specialize(domain_param), cls)
            for name in colnames:
                # Same deal for columns.
                self.assertIs(
                    getattr(generic_non_root, name).specialize(domain_param),
                    getattr(cls, name),
                )

            # Don't allow specializing to any other domain.
            with self.assertRaises(ValueError):
                generic_non_root.specialize(different_domain)

            # Same deal for columns.
            for name in colnames:
                col = getattr(generic_non_root, name)
                with self.assertRaises(ValueError):
                    col.specialize(different_domain)

        do_checks(MyData, ['col1'])
        do_checks(MyDataSubclass, ['col1', 'col2'])


class D(DataSet):
    c1 = Column(float)
    c2 = Column(bool)
    c3 = Column(object)


class InferDomainTestCase(zf.ZiplineTestCase):

    def check(self, inputs, expected):
        result = infer_domain(inputs)
        self.assertIs(result, expected)

    def check_fails(self, inputs, expected_domains):
        with self.assertRaises(AmbiguousDomain) as e:
            infer_domain(inputs)

        err = e.exception
        self.assertEqual(err.domains, expected_domains)

        return err

    def test_all_generic(self):
        self.check([], GENERIC)
        self.check([D.c1], GENERIC)
        self.check([D.c1, D.c2], GENERIC)
        self.check([D.c1, D.c2, D.c3], GENERIC)
        self.check([D.c1.latest, D.c2.latest, D.c3.latest], GENERIC)

    @parameter_space(domain=[US_EQUITIES, GB_EQUITIES])
    def test_all_non_generic(self, domain):
        D_s = D.specialize(domain)
        self.check([D_s.c1], domain)
        self.check([D_s.c1, D_s.c2], domain)
        self.check([D_s.c1, D_s.c2, D_s.c3], domain)
        self.check([D_s.c1, D_s.c2, D_s.c3.latest], domain)

    @parameter_space(domain=[US_EQUITIES, GB_EQUITIES])
    def test_mix_generic_and_specialized(self, domain):
        D_s = D.specialize(domain)
        self.check([D.c1, D_s.c3], domain)
        self.check([D.c1, D.c2, D_s.c3], domain)
        self.check([D.c1, D_s.c2, D_s.c3], domain)

    def test_conflict(self):
        D_US = D.specialize(US_EQUITIES)
        D_CA = D.specialize(CA_EQUITIES)
        D_GB = D.specialize(GB_EQUITIES)

        # Conflict of size 2.
        self.check_fails(
            [D_US.c1, D_CA.c1],
            [CA_EQUITIES, US_EQUITIES],
        )

        # Conflict of size 3.
        self.check_fails(
            [D_US.c1, D_CA.c1, D_GB.c1],
            [CA_EQUITIES, GB_EQUITIES, US_EQUITIES],
        )

        # Make sure each domain only appears once if there are duplicates.
        self.check_fails(
            [D_US.c1, D_CA.c1, D_CA.c2],
            [CA_EQUITIES, US_EQUITIES],
        )

        # Make sure that we filter GENERIC out of the error.
        self.check_fails(
            [D_US.c1, D_CA.c1, D.c1],
            [CA_EQUITIES, US_EQUITIES],
        )

    def test_ambiguous_domain_repr(self):
        err = AmbiguousDomain([CA_EQUITIES, GB_EQUITIES, US_EQUITIES])
        result = str(err)
        expected = dedent(
            """\
            Found terms with conflicting domains:
              - EquityCalendarDomain('CA', 'TSX')
              - EquityCalendarDomain('GB', 'LSE')
              - EquityCalendarDomain('US', 'NYSE')"""
        )
        assert_messages_equal(result, expected)
