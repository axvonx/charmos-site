/* @title: Fixture Types */
#ifndef FIXTURE_TYPES_H
#define FIXTURE_TYPES_H

#define FIXTURE_MAX 42
#define FIXTURE_SQUARE(x) ((x) * (x))

enum color {
    COLOR_RED,
    COLOR_GREEN,
    COLOR_BLUE,
};

/* function-pointer typedef */
typedef int (*compare_fn)(int a, int b);

struct point {
    int x;
    int y;
};

/* struct with an anonymous nested union and a function-pointer member */
struct widget {
    struct point origin;
    enum color color;
    compare_fn cmp;
    union {
        int as_int;
        float as_float;
    } payload;
};

typedef struct widget widget_t;

#endif /* FIXTURE_TYPES_H */
