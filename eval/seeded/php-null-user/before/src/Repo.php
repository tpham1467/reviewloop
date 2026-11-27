<?php

class Repo
{
    public function __construct(private array $rows)
    {
    }

    public function find(int $id): ?array
    {
        return $this->rows[$id] ?? null;
    }
}
