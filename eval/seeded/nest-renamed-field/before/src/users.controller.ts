import { Controller, Get } from '@nestjs/common';
import { UserDto } from './dto/user.dto';

@Controller('users')
export class UsersController {
  @Get('me')
  me(): UserDto {
    return { id: '1', fullName: 'Ada Lovelace' };
  }
}
