from dataclasses import dataclass, field
from typing import Optional


def test(vals: dict[str, int]):
    # Without ** operator
    print("Without ** operator:")
    print_vals(vals["image_delay"], vals["temphum_delay"], vals["status_delay"])

    # With ** operator
    print("With ** operator:")
    print_vals(
        **vals
    )  # **vals == vals['image_delay'], vals['temphum_delay'], vals['status_delay']


@dataclass
class TimelapseConf:
    id: Optional[int] = field(default=1)
    image_delay: int = field(default=0)
    temphum_delay: int = field(default=0)
    status_delay: int = field(default=0)


def print_vals(id: int, image_delay: int, temphum_delay: int, status_delay: int):
    print("Inside print_vals:")
    print("  id:", id)
    print("  image_delay:", image_delay)
    print("  temphum_delay:", temphum_delay)
    print("  status_delay:", status_delay)


def main():
    vals = {"image_delay": 5, "temphum_delay": 10, "status_delay": 10}

    t = TimelapseConf(**vals)
    print_vals(**t.__dict__)


if __name__ == "__main__":
    main()
