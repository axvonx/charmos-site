/* Declarations that only exist after macro expansion. */
typedef unsigned long long u64;

#define strong_int(stem, STEM, base, max_)                                     \
    typedef enum : base {                                                      \
        STEM##_ZERO = 0,                                                       \
        STEM##_MAX = (max_),                                                   \
    } stem##_t

#define GENERATE_GET(type) static inline _Bool type##_get(struct type *obj) { return obj != 0; }

struct thing {
    int refs;
};

strong_int(time_ns, TIME_NS, u64, 0xffffffffffffffffULL);
GENERATE_GET(thing)
