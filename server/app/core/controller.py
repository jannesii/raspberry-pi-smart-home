from ._controller import (
    ACMixin,
    AuthMixin,
    CarHeaterKFactorMixin,
    CarHeaterMixin,
    CarHeaterReadyByMixin,
    ControllerBase,
    LoggingControlMixin,
    LogsMixin,
    MedicineCalculatorMixin,
    MigrationMixin,
    SensorsMixin,
    ThreeDMixin,
    YnabCategorizerMixin,
)


class Controller(
    ControllerBase,
    ACMixin,
    AuthMixin,
    CarHeaterMixin,
    CarHeaterKFactorMixin,
    CarHeaterReadyByMixin,
    LoggingControlMixin,
    LogsMixin,
    MedicineCalculatorMixin,
    MigrationMixin,
    SensorsMixin,
    ThreeDMixin,
    YnabCategorizerMixin,
):
    pass
