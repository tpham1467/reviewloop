import { Controller, Get, Param, UseGuards } from '@nestjs/common';
import { AuthGuard } from './auth.guard';
import { OrdersService } from './orders.service';

@Controller('orders')
export class OrdersController {
  constructor(private readonly orders: OrdersService) {}

  @Get(':id')
  @UseGuards(AuthGuard)
  find(@Param('id') id: string) {
    return this.orders.find(id);
  }
}
