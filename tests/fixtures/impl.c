/* @title: Fixture Implementation */
#include "api.h"

widget_t *widget_registry = 0;

int point_compare(struct point a, struct point b) {
    if (a.x != b.x)
        return a.x < b.x ? -1 : 1;
    if (a.y != b.y)
        return a.y < b.y ? -1 : 1;
    return 0;
}

/* references widget_create's parameters and point_compare across the file */
struct widget *widget_create(struct point origin, enum color color) {
    static struct widget w;
    w.origin = origin;
    w.color = color;
    w.cmp = 0;
    (void)point_compare(origin, origin);
    return &w;
}
