// Host equivalence test for the actual upstream and Metal-safe shape headers.
// The upstream header is a frozen reference; fixed/ShapeFactors.H wraps the
// applied source tree so this harness exercises the patched headers.
// After the build scripts populate extern/, compile and run with:
// clang++ -std=c++17 -I tests/warpx/validation -I extern/warpx/Source \
//   -I extern/amrex/Src/Base tests/warpx/validation/esirkepov-equivalence-harness.cpp \
//   -o /tmp/esirkepov-equivalence && /tmp/esirkepov-equivalence

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

// The test exercises header-only host/device functors.  Keep the host test
// independent of the GPU runtime while satisfying the actual header macros.
#define BL_AMREX_H
#define AMREX_GPU_QUALIFIERS_H_
#define AMREX_GPU_HOST
#define AMREX_GPU_DEVICE
#define AMREX_GPU_HOST_DEVICE
#define AMREX_FORCE_INLINE inline
#define WARPX_TEXT_MSG_H_
#define WARPX_ABORT_WITH_MESSAGE(MSG) std::abort()
namespace amrex {
template <typename... Args>
constexpr void ignore_unused(Args&&...) noexcept {}
} // namespace amrex

#ifndef FIXED_SHAPEFACTORS_SHA256
#define FIXED_SHAPEFACTORS_SHA256 "unstamped"
#endif
#ifndef FIXED_SHAPEFACTORVALUES_SHA256
#define FIXED_SHAPEFACTORVALUES_SHA256 "unstamped"
#endif
#ifndef UPSTREAM_SHAPEFACTORS_SHA256
#define UPSTREAM_SHAPEFACTORS_SHA256 "unstamped"
#endif

namespace upstream {
#include "upstream/ShapeFactors.upstream.H"
} // namespace upstream

// Both trees define the same upstream type names.  The include guard is
// intentionally reopened so this test compiles both real header versions.
#undef WARPX_SHAPEFACTORS_H_
namespace fixed {
#include "fixed/ShapeFactors.H"
} // namespace fixed

namespace {

constexpr double tolerance = 1.0e-13;
constexpr int guard = 32;
constexpr double upstream_canary = -17.25;
constexpr double fixed_canary = 23.5;

struct Counters
{
    std::size_t cases = 0;
    std::size_t slot_comparisons = 0;
    std::size_t accessor_comparisons = 0;
    std::size_t array_comparisons = 0;
    std::size_t array_canary_failures = 0;
    std::array<std::size_t, 3> shift_cases{};
    std::array<std::array<std::size_t, 3>, 5> shift_cases_by_order{};
    std::size_t mismatches = 0;
    std::size_t out_of_segment = 0;
    std::size_t expected_out_of_segment = 0;
};

bool equal(const double lhs, const double rhs)
{
    const double scale = std::max({1.0, std::abs(lhs), std::abs(rhs)});
    return std::abs(lhs - rhs) <= tolerance * scale;
}

template <std::size_t size>
void initialize_guarded(std::array<double, size>& storage,
                        const int visible_begin, const int visible_size,
                        const double canary)
{
    storage.fill(0.0);
    for (int slot = 0; slot < visible_begin; ++slot) {
        storage[slot] = canary;
    }
    for (int slot = visible_begin + visible_size;
         slot < static_cast<int>(size); ++slot) {
        storage[slot] = canary;
    }
}

template <int depos_order>
void report_mismatch(
    const std::string& mode, const double x_new, const double x_old,
    const int i_new, const int slot, const double upstream_value,
    const double fixed_value, const int shift, Counters& counters)
{
    ++counters.mismatches;
    if (counters.mismatches <= 12) {
        std::cerr << "mismatch mode=" << mode << " order=" << depos_order
                  << " x_new=" << std::setprecision(17) << x_new
                  << " x_old=" << x_old << " i_new=" << i_new
                  << " shift=" << shift << " slot=" << slot
                  << " upstream=" << upstream_value
                  << " fixed=" << fixed_value << '\n';
    }
}

template <int depos_order>
int run_case(const double x_new, const double x_old, Counters& ordinary,
    Counters& reduced)
{
    std::array<double, depos_order + 3> upstream_new{};
    const int i_new = upstream::Compute_shape_factor<depos_order>{}(
        upstream_new.data() + 1, x_new);
    std::array<double, depos_order + 3 + 2 * guard> upstream_old_storage{};
    std::array<double, depos_order + 3 + 2 * guard> fixed_old_storage{};
    initialize_guarded(upstream_old_storage, guard, depos_order + 3,
                       upstream_canary);
    initialize_guarded(fixed_old_storage, guard, depos_order + 3,
                       fixed_canary);
    double* const upstream_old = upstream_old_storage.data() + guard;
    double* const fixed_old = fixed_old_storage.data() + guard;
    const int i_old = upstream::Compute_shifted_shape_factor<depos_order>{}(
        upstream_old, x_old, i_new);
    const int reference_shift = i_old - i_new;
    if (reference_shift < -1 || reference_shift > 1) {
        return 0;
    }
    const int fixed_old_index = fixed::Compute_shifted_shape_factor_index<depos_order>{}(x_old);
    const int fixed_old_array_index = fixed::Compute_shifted_shape_factor<depos_order>{}(
        fixed_old, x_old, i_new);
    const int shift = reference_shift;
    ++ordinary.shift_cases[shift + 1];
    ++ordinary.shift_cases_by_order[depos_order][shift + 1];

    const int fixed_new_index = fixed::Compute_shape_factor_index<depos_order>{}(x_new);
    if (fixed_new_index != i_new || fixed_old_index != i_old
        || fixed_old_array_index != i_old) {
        std::cerr << "index mismatch order=" << depos_order
                  << " x_new=" << std::setprecision(17) << x_new
                  << " x_old=" << x_old << " upstream_new=" << i_new
                  << " fixed_new=" << fixed_new_index
                  << " upstream_old=" << i_old
                  << " fixed_old=" << fixed_old_index
                  << " fixed_old_array=" << fixed_old_array_index << '\n';
        ++ordinary.mismatches;
    }

    for (int slot = 0; slot <= depos_order + 2; ++slot) {
        ++ordinary.array_comparisons;
        if (!equal(upstream_old[slot], fixed_old[slot])) {
            report_mismatch<depos_order>("array-old", x_new, x_old, i_new,
                                         slot, upstream_old[slot],
                                         fixed_old[slot], shift, ordinary);
        }
    }
    bool canary_corrupted = false;
    for (int slot = 0; slot < guard; ++slot) {
        canary_corrupted = canary_corrupted
            || upstream_old_storage[slot] != upstream_canary
            || fixed_old_storage[slot] != fixed_canary;
    }
    for (int slot = guard + depos_order + 3;
         slot < static_cast<int>(upstream_old_storage.size()); ++slot) {
        canary_corrupted = canary_corrupted
            || upstream_old_storage[slot] != upstream_canary
            || fixed_old_storage[slot] != fixed_canary;
    }
    if (canary_corrupted) {
        ++ordinary.array_canary_failures;
        ++ordinary.mismatches;
        std::cerr << "array canary corrupted order=" << depos_order
                  << " x_new=" << std::setprecision(17) << x_new
                  << " x_old=" << x_old << " shift=" << shift << '\n';
    }

    const fixed::Compute_esirkepov_shape_accessor<depos_order, false> ordinary_new_accessor{
        x_new, x_old, i_new, false, false};
    const fixed::Compute_esirkepov_shape_accessor<depos_order, true> ordinary_old_accessor{
        x_new, x_old, i_new, false, false};

    ++ordinary.cases;
    for (int slot = 0; slot <= depos_order + 2; ++slot) {
        ++ordinary.slot_comparisons;
        const double fixed_new = fixed::Compute_esirkepov_shape_value<depos_order>{}
            .new_value(x_new, i_new, slot, false);
        const double fixed_old_value = fixed::Compute_esirkepov_shape_value<depos_order>{}
            .old_value(x_old, i_new, slot, false);
        const double accessor_new = ordinary_new_accessor[slot];
        const double accessor_old = ordinary_old_accessor[slot];
        const double accessor_old_method = ordinary_new_accessor.old(slot);
        ordinary.accessor_comparisons += 3;
        if (!equal(upstream_new[slot], fixed_new)) {
            report_mismatch<depos_order>("ordinary-new", x_new, x_old, i_new,
                                         slot, upstream_new[slot], fixed_new,
                                         shift, ordinary);
        }
        if (!equal(upstream_new[slot], accessor_new)) {
            report_mismatch<depos_order>("accessor-new", x_new, x_old, i_new,
                                         slot, upstream_new[slot], accessor_new,
                                         shift, ordinary);
        }
        if (!equal(upstream_old[slot], accessor_old)) {
            report_mismatch<depos_order>("accessor-old", x_new, x_old, i_new,
                                         slot, upstream_old[slot], accessor_old,
                                         shift, ordinary);
        }
        if (!equal(upstream_old[slot], accessor_old_method)) {
            report_mismatch<depos_order>("accessor-old-method", x_new, x_old,
                                         i_new, slot, upstream_old[slot],
                                         accessor_old_method, shift, ordinary);
        }
        if (!equal(upstream_old[slot], fixed_old_value)) {
            report_mismatch<depos_order>("ordinary-old", x_new, x_old, i_new,
                                         slot, upstream_old[slot], fixed_old_value,
                                         shift, ordinary);
        }
    }

    // Reduced shape is compiled only for orders > 1.  Use a guarded backing
    // store because the upstream helper receives array+offset; the segment
    // copied below is exactly the real sx[0..depos_order+2] array.
    if constexpr (depos_order > 1) {
        constexpr int offset = depos_order / 2;
        std::array<double, depos_order + 3 + 2 * guard> guarded_new{};
        std::array<double, depos_order + 3 + 2 * guard> guarded_old{};
        initialize_guarded(guarded_new, guard, depos_order + 3,
                           upstream_canary);
        initialize_guarded(guarded_old, guard, depos_order + 3,
                           upstream_canary);
        double* const segment_new = guarded_new.data() + guard;
        double* const segment_old = guarded_old.data() + guard;
        upstream::Compute_shifted_shape_factor<1>{}(
            segment_new + offset, x_new, i_new + offset);
        upstream::Compute_shifted_shape_factor<1>{}(
            segment_old + offset, x_old, i_new + offset);

        // The reduced order-1 reference writes through an offset pointer.
        // A write outside the high-order visible segment is expected upstream
        // pointer-contract behavior, so classify and check it separately from
        // visible-slot equivalence.
        const int reduced_new_index = static_cast<int>(std::floor(x_new));
        const int reduced_old_index = static_cast<int>(std::floor(x_old));
        bool expected_outside = false;
        for (const int index : {reduced_new_index, reduced_old_index}) {
            for (int slot = 0; slot <= 1; ++slot) {
                const int absolute_slot = 1 + index - i_new + slot;
                expected_outside = expected_outside
                    || absolute_slot < 0 || absolute_slot > depos_order + 2;
            }
        }

        bool outside = false;
        for (int slot = 0; slot < guard; ++slot) {
            outside = outside || guarded_new[slot] != upstream_canary
                || guarded_old[slot] != upstream_canary;
        }
        for (int slot = guard + depos_order + 3;
             slot < static_cast<int>(guarded_new.size()); ++slot) {
            outside = outside || guarded_new[slot] != upstream_canary
                || guarded_old[slot] != upstream_canary;
        }
        if (outside != expected_outside) {
            ++reduced.mismatches;
            std::cerr << "unexpected reduced out-of-segment classification order="
                      << depos_order << " x_new=" << std::setprecision(17)
                      << x_new << " x_old=" << x_old << '\n';
        }
        if (expected_outside) {
            ++reduced.expected_out_of_segment;
        }
        if (outside) {
            ++reduced.out_of_segment;
        }

        const fixed::Compute_esirkepov_shape_accessor<depos_order, false> reduced_new_accessor{
            x_new, x_old, i_new, true, true};
        const fixed::Compute_esirkepov_shape_accessor<depos_order, true> reduced_old_accessor{
            x_new, x_old, i_new, true, true};

        ++reduced.cases;
        for (int slot = 0; slot <= depos_order + 2; ++slot) {
            ++reduced.slot_comparisons;
            const double fixed_new = fixed::Compute_esirkepov_shape_value<depos_order>{}
                .new_value(x_new, i_new, slot, true);
            const double fixed_old = fixed::Compute_esirkepov_shape_value<depos_order>{}
                .old_value(x_old, i_new, slot, true);
            const double accessor_new = reduced_new_accessor[slot];
            const double accessor_old = reduced_old_accessor[slot];
            const double accessor_old_method = reduced_new_accessor.old(slot);
            reduced.accessor_comparisons += 3;
            if (!equal(segment_new[slot], fixed_new)) {
                report_mismatch<depos_order>("reduced-new", x_new, x_old,
                                             i_new, slot, segment_new[slot],
                                             fixed_new, shift, reduced);
            }
            if (!equal(segment_old[slot], fixed_old)) {
                report_mismatch<depos_order>("reduced-old", x_new, x_old,
                                             i_new, slot, segment_old[slot],
                                             fixed_old, shift, reduced);
            }
            if (!equal(segment_new[slot], accessor_new)) {
                report_mismatch<depos_order>("reduced-accessor-new", x_new,
                                             x_old, i_new, slot,
                                             segment_new[slot], accessor_new,
                                             shift, reduced);
            }
            if (!equal(segment_old[slot], accessor_old)) {
                report_mismatch<depos_order>("reduced-accessor-old", x_new,
                                             x_old, i_new, slot,
                                             segment_old[slot], accessor_old,
                                             shift, reduced);
            }
            if (!equal(segment_old[slot], accessor_old_method)) {
                report_mismatch<depos_order>("reduced-accessor-old-method",
                                             x_new, x_old, i_new, slot,
                                             segment_old[slot],
                                             accessor_old_method, shift, reduced);
            }
        }
    }

    return 0;
}

template <int depos_order>
void run_order(Counters& ordinary, Counters& reduced)
{
    // Dense positions include negative coordinates, cell boundaries, zero,
    // and moves that cross a centering boundary or the cylindrical origin.
    for (int new_tick = -800; new_tick <= 800; ++new_tick) {
        const double x_new = static_cast<double>(new_tick) / 100.0;
        for (int delta_tick = -95; delta_tick <= 95; ++delta_tick) {
            const double x_old = x_new + static_cast<double>(delta_tick) / 100.0;
            run_case<depos_order>(x_new, x_old, ordinary, reduced);
        }
    }
}

} // namespace

int main()
{
    Counters ordinary;
    Counters reduced;
    run_order<0>(ordinary, reduced);
    run_order<1>(ordinary, reduced);
    run_order<2>(ordinary, reduced);
    run_order<3>(ordinary, reduced);
    run_order<4>(ordinary, reduced);

    std::size_t coverage_failures = 0;
    std::cout << std::setprecision(17)
              << "tolerance=" << tolerance << '\n'
              << "fixed_shape_factors_sha256=" << FIXED_SHAPEFACTORS_SHA256 << '\n'
              << "fixed_shape_factor_values_sha256=" << FIXED_SHAPEFACTORVALUES_SHA256 << '\n'
              << "upstream_shape_factors_sha256=" << UPSTREAM_SHAPEFACTORS_SHA256 << '\n'
              << "ordinary_cases=" << ordinary.cases << '\n'
              << "ordinary_slot_comparisons=" << ordinary.slot_comparisons << '\n'
              << "ordinary_accessor_comparisons=" << ordinary.accessor_comparisons << '\n'
              << "ordinary_array_comparisons=" << ordinary.array_comparisons << '\n'
              << "ordinary_array_canary_failures=" << ordinary.array_canary_failures << '\n'
              << "shift_minus_one_cases=" << ordinary.shift_cases[0] << '\n'
              << "shift_zero_cases=" << ordinary.shift_cases[1] << '\n'
              << "shift_plus_one_cases=" << ordinary.shift_cases[2] << '\n'
              << "reduced_cases=" << reduced.cases << '\n'
              << "reduced_slot_comparisons=" << reduced.slot_comparisons << '\n'
              << "reduced_accessor_comparisons=" << reduced.accessor_comparisons << '\n'
              << "reduced_out_of_segment=" << reduced.out_of_segment << '\n'
              << "reduced_expected_out_of_segment=" << reduced.expected_out_of_segment << '\n'
              << "ordinary_mismatches=" << ordinary.mismatches << '\n'
              << "reduced_mismatches=" << reduced.mismatches << '\n'
              << "coverage=";
    for (int order = 0; order <= 4; ++order) {
        for (int shift = -1; shift <= 1; ++shift) {
            const auto count = ordinary.shift_cases_by_order[order][shift + 1];
            std::cout << (order == 0 && shift == -1 ? "" : ";")
                      << "order" << order << ":shift" << shift << "=" << count;
            if (count == 0) {
                ++coverage_failures;
            }
        }
    }
    std::cout << '\n'
              << "coverage_failures=" << coverage_failures << '\n'
              << "mismatches=" << (ordinary.mismatches + reduced.mismatches) << '\n'
              << "status=" << ((ordinary.mismatches == 0 && reduced.mismatches == 0
                                  && coverage_failures == 0)
                                  ? "PASS" : "FAIL") << '\n';
    return (ordinary.mismatches == 0 && reduced.mismatches == 0
            && coverage_failures == 0) ? EXIT_SUCCESS : EXIT_FAILURE;
}
